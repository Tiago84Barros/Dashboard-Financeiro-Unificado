"""
data_pipeline/utils/date_utils.py
Utilitários de data e frequência para o pipeline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Mapeia frequência textual → timedelta
_FREQ_MAP: dict[str, timedelta] = {
    "tempo_real":  timedelta(minutes=5),
    "horario":     timedelta(hours=1),
    "diario":      timedelta(days=1),
    "semanal":     timedelta(weeks=1),
    "quinzenal":   timedelta(days=15),
    "mensal":      timedelta(days=30),
    "trimestral":  timedelta(days=90),
    "semestral":   timedelta(days=180),
    "anual":       timedelta(days=365),
}


def frequency_to_timedelta(frequency: str) -> timedelta:
    return _FREQ_MAP.get(frequency.lower(), timedelta(days=1))


def next_expected_update(last_success_at: datetime | None, frequency: str) -> datetime | None:
    if last_success_at is None:
        return None
    delta = frequency_to_timedelta(frequency)
    ts = last_success_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts + delta


def should_run_job(registry_item: dict, force: bool = False) -> bool:
    """
    Decide se um job deve ser executado com base na frequência e última atualização.
    force=True sempre retorna True para jobs ativos.
    """
    if not registry_item.get("is_active", True):
        return False
    if force:
        return True

    frequency = registry_item.get("frequency", "diario")
    last_success: datetime | None = registry_item.get("last_success_at")

    if last_success is None:
        return True

    if isinstance(last_success, str):
        try:
            last_success = datetime.fromisoformat(last_success)
        except ValueError:
            return True

    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)

    now = datetime.now(tz=timezone.utc)
    delta = frequency_to_timedelta(frequency)
    return (now - last_success) >= delta


def freshness_label(
    last_success_at: datetime | None,
    frequency: str,
    status: str | None = None,
) -> str:
    """Retorna 'updated', 'attention', 'outdated', 'never_updated' ou 'error'."""
    if status in ("failed", "error"):
        return "error"
    if last_success_at is None:
        return "never_updated"

    if isinstance(last_success_at, str):
        try:
            last_success_at = datetime.fromisoformat(last_success_at)
        except ValueError:
            return "never_updated"

    if last_success_at.tzinfo is None:
        last_success_at = last_success_at.replace(tzinfo=timezone.utc)

    now = datetime.now(tz=timezone.utc)
    delta = frequency_to_timedelta(frequency)
    elapsed = now - last_success_at

    if elapsed <= delta:
        return "updated"
    elif elapsed <= delta * 2:
        return "attention"
    else:
        return "outdated"


def fmt_datetime_br(dt: datetime | None) -> str:
    if dt is None:
        return "Nunca"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    return dt.strftime("%d/%m/%Y %H:%M")
