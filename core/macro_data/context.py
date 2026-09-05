"""Recuperação macro mínima e rastreável para prompts de LLM.

O módulo recebe somente linhas já normalizadas do banco. Nenhum payload externo
ou conteúdo arbitrário de metadados é encaminhado ao modelo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

MAX_CONTEXT_ITEMS = 20
MAX_TEXT_LENGTH = 180


def build_macro_context(
    rows: Iterable[Mapping[str, object]],
    *,
    now: datetime | None = None,
    limit: int = MAX_CONTEXT_ITEMS,
) -> tuple[dict[str, object], ...]:
    """Cria fatos citáveis, sem inferir causalidade ou recomendação.

    ``rows`` deve vir de uma consulta autorizada e já limitada ao ativo/país da
    pergunta. Entradas sem valor ou sem procedência são excluídas, em vez de
    serem convertidas para zero.
    """
    now = now or datetime.now(timezone.utc)
    candidates: list[dict[str, object]] = []
    for row in rows:
        value, source, period = (
            row.get("value"),
            row.get("provider"),
            row.get("reference_period"),
        )
        if value is None or not source or not period:
            continue
        retrieved = _as_utc(row.get("retrieved_at"))
        if retrieved is None:
            continue
        name = _safe_text(row.get("name") or row.get("provider_code") or "Indicador")
        candidates.append(
            {
                "indicator": name,
                "provider": _safe_text(source),
                "provider_code": _safe_text(row.get("provider_code")),
                "country_code": _safe_text(row.get("country_code")) or None,
                "reference_period": str(period),
                "value": value,
                "unit": _safe_text(row.get("unit")) or "unidade não informada",
                "released_at": _iso(row.get("released_at")),
                "retrieved_at": retrieved.isoformat(),
                "age_hours": round(
                    max(0.0, (now - retrieved).total_seconds() / 3600), 1
                ),
                "is_forecast": bool(row.get("is_forecast")),
                "is_preliminary": bool(row.get("is_preliminary")),
                "vintage_date": str(row.get("vintage_date"))
                if row.get("vintage_date")
                else None,
                "limitations": _limitations(row, now, retrieved),
            }
        )
    candidates.sort(key=lambda x: (x["age_hours"], x["indicator"], x["provider_code"]))
    return tuple(candidates[: max(0, min(limit, MAX_CONTEXT_ITEMS))])


def latest_macro_context(
    engine, *, now: datetime | None = None
) -> tuple[dict[str, object], ...]:
    """Lê somente a versão mais recente por série para contexto de LLM."""
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = (
            conn.execute(
                text("""
            SELECT DISTINCT ON (o.provider, o.provider_code, COALESCE(o.country_code, ''))
              i.name, i.unit, o.provider, o.provider_code, o.country_code,
              o.reference_period, o.value, o.released_at, o.retrieved_at,
              o.is_forecast, o.is_preliminary, o.vintage_date
            FROM macro_observations o
            LEFT JOIN macro_indicators i ON i.provider=o.provider AND i.provider_code=o.provider_code
             AND COALESCE(i.country_code, '')=COALESCE(o.country_code, '')
            WHERE o.value IS NOT NULL
            ORDER BY o.provider, o.provider_code, COALESCE(o.country_code, ''),
              COALESCE(o.vintage_date, o.reference_period) DESC, o.retrieved_at DESC
            LIMIT :limit
        """),
                {"limit": MAX_CONTEXT_ITEMS},
            )
            .mappings()
            .all()
        )
    return build_macro_context(rows, now=now)


def format_macro_context(facts: Iterable[Mapping[str, object]]) -> tuple[str, ...]:
    """Linhas factuais para prompt ancorado, sem instruções externas."""
    lines = []
    for fact in facts:
        limitations = "; ".join(str(note) for note in fact.get("limitations", ())[:3])
        lines.append(
            f"MACRO · {fact['indicator']} [{fact['provider']}] · período {fact['reference_period']} · "
            f"valor {fact['value']} {fact['unit']} · coletado {fact['retrieved_at']}"
            + (f" · LIMITAÇÃO: {limitations}" if limitations else "")
        )
    return tuple(lines)


def _safe_text(value: object) -> str:
    # Metadados externos são evidência, não instruções: remove controles e corta.
    return " ".join(str(value or "").replace("\x00", " ").split())[:MAX_TEXT_LENGTH]


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _iso(value: object) -> str | None:
    parsed = _as_utc(value)
    return parsed.isoformat() if parsed else None


def _limitations(
    row: Mapping[str, object], now: datetime, retrieved: datetime
) -> tuple[str, ...]:
    notes = []
    if now - retrieved > timedelta(days=35):
        notes.append("dado potencialmente desatualizado")
    if row.get("is_forecast"):
        notes.append("projeção, não observação realizada")
    if row.get("is_preliminary"):
        notes.append("valor preliminar sujeito a revisão")
    if row.get("vintage_date") is None:
        notes.append("vintage não informado pela fonte")
    return tuple(notes)
