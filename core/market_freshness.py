"""Classificação explícita de frescor para preços e snapshots."""

from __future__ import annotations

from datetime import date, datetime, timezone


MAX_QUOTE_AGE_DAYS = 5


def _as_utc_datetime(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def classificar_cotacao(
    timestamp: datetime | date | None,
    agora: datetime | date | None = None,
    max_age_days: int = MAX_QUOTE_AGE_DAYS,
) -> str:
    """Retorna ``fresh``, ``stale``, ``missing`` ou ``invalid``."""
    ts = _as_utc_datetime(timestamp)
    ref = _as_utc_datetime(agora) or datetime.now(timezone.utc)
    if ts is None:
        return "missing"
    idade_dias = (ref.date() - ts.date()).days
    if idade_dias < 0 or max_age_days < 0:
        return "invalid"
    return "fresh" if idade_dias <= max_age_days else "stale"


def intervalo_referencia(valores: list[datetime | date | None]) -> tuple[date | None, date | None]:
    datas = [dt.date() for v in valores if (dt := _as_utc_datetime(v)) is not None]
    return (min(datas), max(datas)) if datas else (None, None)
