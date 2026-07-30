"""Diagnóstico de cobertura look-through dos FIIs.

Somente exposições nominais observadas contam como cobertura. Métricas agregadas,
como concentração do maior inquilino, não são convertidas em identidades.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

DIMENSION_FIELDS = {
    "sector": "sector",
    "issuer": "issuers",
    "tenant": "tenants",
    "debtor": "debtors",
    "indexer": "indexers",
    "region": "regions",
}
APPLICABLE_TYPES = {
    "sector": {"tijolo", "hibrido"},
    "tenant": {"tijolo", "hibrido"},
    "region": {"tijolo", "hibrido"},
    "issuer": {"papel", "hibrido"},
    "debtor": {"papel", "hibrido"},
    "indexer": {"papel", "hibrido"},
}
REQUIRED_DIMENSIONS = ("sector", "issuer")
SUPPLEMENTARY_DIMENSIONS = ("tenant", "debtor", "indexer", "region")
SOURCE_LIMITATIONS = {
    "tenant": (
        "O informe trimestral estruturado da CVM divulga imóvel, setor e "
        "percentual de receita, mas não identifica nominalmente o locatário."
    ),
}


def _valid_mapping(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    valid = 0
    for name, weight in value.items():
        try:
            number = float(weight)
        except (TypeError, ValueError):
            continue
        if str(name).strip() and math.isfinite(number) and number > 0:
            valid += 1
    return valid > 0


def has_observed_dimension(row: dict, dimension: str) -> bool:
    """Retorna True apenas para exposição explicitamente observada."""
    field = DIMENSION_FIELDS[dimension]
    value = row.get(field)
    if field == dimension:
        return value not in (None, "")
    return _valid_mapping(value)


def summarize_lookthrough_coverage(
    rows: Iterable[dict], *, min_coverage: float = .80,
) -> dict:
    records = list(rows)
    dimensions: dict[str, dict] = {}
    for dimension in DIMENSION_FIELDS:
        applicable_types = APPLICABLE_TYPES[dimension]
        applicable = [
            row for row in records
            if str(row.get("tipo") or "").strip().lower() in applicable_types
        ]
        observed = [
            row for row in applicable if has_observed_dimension(row, dimension)
        ]
        coverage = len(observed) / len(applicable) if applicable else None
        required = dimension in REQUIRED_DIMENSIONS
        can_enforce = coverage is not None and coverage >= min_coverage
        dimensions[dimension] = {
            "required": required,
            "applicable_count": len(applicable),
            "observed_count": len(observed),
            "coverage": coverage,
            "minimum": min_coverage,
            "can_enforce": can_enforce,
            "status": (
                "ok" if can_enforce
                else "blocked" if required
                else "partial"
            ),
            "source_limitation": SOURCE_LIMITATIONS.get(dimension),
        }
    blockers = [
        dimension for dimension in REQUIRED_DIMENSIONS
        if not dimensions[dimension]["can_enforce"]
    ]
    return {
        "row_count": len(records),
        "minimum": min_coverage,
        "dimensions": dimensions,
        "required_blockers": blockers,
        "required_ready": not blockers,
        "limitations": [
            value for value in SOURCE_LIMITATIONS.values() if value
        ],
    }
