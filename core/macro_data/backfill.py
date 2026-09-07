"""Validação determinística dos parâmetros da carga histórica macro."""

from __future__ import annotations

from datetime import date


def parse_backfill_period(
    start: str | None, end: str | None, *, today: date | None = None
) -> tuple[date, date]:
    today = today or date.today()
    try:
        start_date = date.fromisoformat(start) if start else date(2010, 1, 1)
        end_date = date.fromisoformat(end) if end else today
    except ValueError as exc:
        raise ValueError("datas devem usar o formato AAAA-MM-DD") from exc
    if start_date > end_date:
        raise ValueError("a data inicial não pode ser posterior à final")
    if start_date.year < 1900 or end_date > today:
        raise ValueError("período histórico fora do intervalo permitido")
    return start_date, end_date
