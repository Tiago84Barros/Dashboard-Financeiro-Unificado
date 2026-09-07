"""Cópia somente leitura do histórico macro doméstico para o Docker local."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from sqlalchemy import text

from core.macro_data.models import MacroIndicator, MacroObservation
from core.macro_data.repository import append_observation, upsert_indicator

_FIELDS = {
    "selic": ("monetary_policy", "% a.a.", "Selic anual"),
    "ipca": ("inflation", "% a.a.", "IPCA anual"),
    "cambio": ("currencies", "BRL por USD", "Câmbio USD/BRL"),
    "balanca_comercial": ("economic_activity", "unidade legada", "Balança comercial"),
    "icc": ("economic_activity", "índice", "Índice de confiança do consumidor"),
    "icc_delta": ("economic_activity", "pontos", "Variação do ICC"),
    "pib": ("economic_activity", "% a.a.", "PIB anual"),
    "divida_publica": ("debt", "unidade legada", "Dívida pública"),
    "juros_real_ex_ante": ("monetary_policy", "% a.a.", "Juro real ex ante"),
}


def _normalized_value(field: str, value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    if field == "selic" and abs(result) <= 1:
        return result * 100.0
    return result


def sync_domestic_macro(*, source_engine, local_engine) -> int:
    """Lê ``public.macro`` sem escrever na origem e persiste só no Docker."""
    with source_engine.connect() as source:
        rows = source.execute(text("""
            SELECT ano, selic, ipca, cambio, balanca_comercial, icc, icc_delta,
                   pib, divida_publica, juros_real_ex_ante
              FROM public.macro
             ORDER BY ano
        """)).mappings().all()
    retrieved_at = datetime.now(timezone.utc)
    inserted = 0
    with local_engine.begin() as destination:
        for field, (category, unit, name) in _FIELDS.items():
            upsert_indicator(destination, MacroIndicator(
                canonical_code=f"app4_domestic.{field}",
                provider_code=field,
                provider="app4_domestic",
                name=name,
                description=(
                    "Cópia local da tabela histórica public.macro; a tabela de "
                    "origem não preserva a URL nem a data exata de divulgação."
                ),
                country_code="BRA",
                category=category,
                unit=unit,
                frequency="annual",
                source_organization="APP4 public.macro (fonte primária não preservada)",
            ))
        for row in rows:
            try:
                year = int(row["ano"])
                reference_period = date(year, 12, 31)
            except (TypeError, ValueError):
                continue
            for field in _FIELDS:
                value = _normalized_value(field, row.get(field))
                if value is None:
                    continue
                inserted += append_observation(destination, MacroObservation(
                    provider="app4_domestic",
                    provider_code=field,
                    country_code="BRA",
                    reference_period=reference_period,
                    value=value,
                    retrieved_at=retrieved_at,
                    status="legacy_source_provenance_incomplete",
                    raw_payload_reference=f"app4_main:public.macro:{year}:{field}",
                ))
    return inserted
