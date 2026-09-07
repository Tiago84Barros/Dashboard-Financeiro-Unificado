"""Política determinística de atualização para séries macro já conhecidas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_INTERVALS = {
    "intraday": timedelta(hours=1),
    "daily": timedelta(hours=6),
    "weekly": timedelta(hours=12),
    "monthly": timedelta(hours=24),
    "quarterly": timedelta(hours=48),
    "annual": timedelta(days=7),
    "irregular": timedelta(hours=24),
}


def observation_due(
    last_retrieved_at: datetime | None,
    frequency: str | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Indica se uma nova consulta é necessária; todos os cálculos usam UTC."""
    if last_retrieved_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now deve possuir timezone")
    if last_retrieved_at.tzinfo is None:
        last_retrieved_at = last_retrieved_at.replace(tzinfo=timezone.utc)
    interval = _INTERVALS.get(str(frequency or "").lower(), _INTERVALS["irregular"])
    return now >= last_retrieved_at.astimezone(timezone.utc) + interval
