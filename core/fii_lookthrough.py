"""Diagnóstico de cobertura look-through dos FIIs.

Somente exposições nominais observadas contam como cobertura. Métricas agregadas,
como concentração do maior inquilino, não são convertidas em identidades.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from core.fii_imoveis import regiao_por_uf

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
QUALITY_PREFERENCE_DIMENSIONS = ("debtor", "indexer", "region")
MATERIAL_EXPOSURE_THRESHOLD = .20
SOURCE_LIMITATIONS = {
    "tenant": (
        "O informe trimestral estruturado da CVM divulga imóvel, setor e "
        "percentual de receita, mas não identifica nominalmente o locatário."
    ),
}
_PROPERTY_DIMENSIONS = {"sector", "tenant", "region"}
_CREDIT_DIMENSIONS = {"issuer", "debtor", "indexer"}
_MACRO_REGIONS = {
    "NORTE": "Norte",
    "NORDESTE": "Nordeste",
    "CENTRO-OESTE": "Centro-Oeste",
    "CENTRO OESTE": "Centro-Oeste",
    "SUDESTE": "Sudeste",
    "SUL": "Sul",
}


def _fraction(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number / 100 if number > 1 else number


def dimension_is_applicable(
    row: dict,
    dimension: str,
    *,
    materiality: float = MATERIAL_EXPOSURE_THRESHOLD,
) -> bool:
    """Decide aplicabilidade sem transformar ausência em exposição.

    Fundos classificados como híbridos podem ser, na prática, combinações de
    papel+FoF ou imóveis+FoF. Quando a composição está observada, uma dimensão
    só é aplicável se a exposição econômica correspondente for material. Sem
    composição observada, o comportamento conservador anterior é preservado.
    """
    fii_type = str(row.get("tipo") or "").strip().lower()
    if fii_type not in APPLICABLE_TYPES.get(dimension, set()):
        return False
    if fii_type != "hibrido":
        return True
    if dimension in _PROPERTY_DIMENSIONS:
        observed = _fraction(row.get("pct_imoveis"))
    elif dimension in _CREDIT_DIMENSIONS:
        observed = _fraction(row.get("pct_papel"))
    else:
        observed = None
    return observed >= materiality if observed is not None else True


def _canonical_region(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    from_uf = regiao_por_uf(text)
    if from_uf:
        return from_uf
    normalized = text.upper().replace("_", " ").replace("-", " ")
    normalized = " ".join(normalized.split())
    return _MACRO_REGIONS.get(normalized)


def normalized_dimension_mapping(row: dict, dimension: str) -> dict[str, float]:
    """Limpa exposições e consolida UFs na mesma macrorregião do IBGE."""
    field = DIMENSION_FIELDS[dimension]
    value = row.get(field)
    if field == dimension or not isinstance(value, dict):
        return {}
    normalized: dict[str, float] = {}
    for raw_name, raw_weight in value.items():
        try:
            number = float(raw_weight)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number) or number <= 0:
            continue
        name = (
            _canonical_region(raw_name)
            if dimension == "region"
            else str(raw_name).strip()
        )
        if not name:
            continue
        normalized[name] = normalized.get(name, 0.0) + number
    return normalized


def has_observed_dimension(row: dict, dimension: str) -> bool:
    """Retorna True apenas para exposição explicitamente observada."""
    field = DIMENSION_FIELDS[dimension]
    value = row.get(field)
    if field == dimension:
        return value not in (None, "")
    return bool(normalized_dimension_mapping(row, dimension))


def supplementary_evidence_score(row: dict) -> float:
    """Fração de dimensões suplementares aplicáveis com identidade observada."""
    applicable = [
        dimension for dimension in QUALITY_PREFERENCE_DIMENSIONS
        if dimension_is_applicable(row, dimension)
    ]
    if not applicable:
        return 0.0
    return sum(
        bool(normalized_dimension_mapping(row, dimension))
        for dimension in applicable
    ) / len(applicable)


def summarize_lookthrough_coverage(
    rows: Iterable[dict], *, min_coverage: float = .80,
) -> dict:
    records = list(rows)
    dimensions: dict[str, dict] = {}
    for dimension in DIMENSION_FIELDS:
        applicable_types = APPLICABLE_TYPES[dimension]
        applicable = [
            row for row in records
            if (
                str(row.get("tipo") or "").strip().lower() in applicable_types
                and dimension_is_applicable(row, dimension)
            )
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
