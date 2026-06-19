"""
data_pipeline/quality/comparer.py
Comparação entre fontes — adaptador fino sobre core.data_healing.

Regra (definida em core.data_healing.resolve_field): banco × Fundamentus ×
Status Invest; exige ≥2 fontes válidas concordantes; em divergência prioriza
Fundamentus/Status Invest; nunca substitui dado confiável por menos confiável.
"""
from __future__ import annotations

from core.data_healing import (  # re-export (responsabilidade única: comparar)
    FieldResolution,
    preview_healing,
    proposals_only,
    resolve_field,
    resolve_ticker,
)

__all__ = [
    "FieldResolution", "preview_healing", "proposals_only",
    "resolve_field", "resolve_ticker",
]
