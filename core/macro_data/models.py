"""Contratos tipados e imutáveis da camada macro."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal

Frequency = Literal[
    "intraday", "daily", "weekly", "monthly", "quarterly", "annual", "irregular"
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProviderHealth:
    provider_id: str
    available: bool
    detail: str
    checked_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MacroIndicator:
    canonical_code: str
    provider_code: str
    provider: str
    name: str
    category: str
    unit: str
    frequency: Frequency
    source_organization: str
    country_code: str | None = None
    description: str | None = None
    seasonal_adjustment: str | None = None
    source_url: str | None = None
    active: bool = True


@dataclass(frozen=True)
class MacroObservation:
    provider: str
    provider_code: str
    reference_period: date
    value: float | None
    retrieved_at: datetime
    released_at: datetime | None = None
    country_code: str | None = None
    status: str | None = None
    provider_updated_at: datetime | None = None
    is_preliminary: bool = False
    is_forecast: bool = False
    vintage_date: date | None = None
    revision_number: int | None = None
    raw_payload_reference: str | None = None


@dataclass(frozen=True)
class MacroRelease:
    provider: str
    country_code: str
    event_name: str
    scheduled_at: datetime
    retrieved_at: datetime
    status: str
    actual_value: float | None = None
    previous_value: float | None = None
    revised_previous_value: float | None = None
    consensus_value: float | None = None
    forecast_value: float | None = None
    unit: str | None = None
    importance: int | None = None
    raw_payload_reference: str | None = None


@dataclass(frozen=True)
class ObservationQuery:
    provider_code: str
    country_code: str | None = None
    start: date | None = None
    end: date | None = None
    vintage_date: date | None = None
