"""Mapeamento explícito de séries externas para a taxonomia macro do APP4."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from core.macro_data.models import MacroIndicator

CATEGORIES = frozenset(
    {
        "inflation",
        "economic_activity",
        "employment",
        "monetary_policy",
        "credit_liquidity",
        "fiscal",
        "external_sector",
        "currencies",
        "debt",
        "housing",
        "confidence",
        "trade",
        "commodities",
        "financial_conditions",
        "banking_risk",
        "systemic_risk",
    }
)


def map_indicator(
    indicator: MacroIndicator, mappings: Mapping[str, Mapping[str, str]]
) -> MacroIndicator:
    """Aplica apenas mapeamento declarado; ausência preserva ``unmapped``."""
    mapping = mappings.get(f"{indicator.provider}.{indicator.provider_code}")
    if not mapping:
        return indicator
    category = mapping.get("category")
    canonical_code = mapping.get("canonical_code")
    if category not in CATEGORIES or not canonical_code:
        return indicator
    return replace(indicator, category=category, canonical_code=canonical_code)
