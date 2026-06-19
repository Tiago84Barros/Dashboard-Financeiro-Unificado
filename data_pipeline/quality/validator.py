"""
data_pipeline/quality/validator.py
Auditoria de qualidade — adaptador fino sobre core.data_quality (fonte única).

Cobre: nulos/ausentes, zeros indevidos, múltiplos impossíveis/fora de faixa,
indicadores incompatíveis, DRE incompleta, setor/segmento ausente, tickers
duplicados e completude de campos críticos. NÃO duplica lógica: delega tudo a
core.data_quality.
"""
from __future__ import annotations

import pandas as pd

from core.data_quality import (  # re-export (responsabilidade única: validar)
    CANONICAL_RANGES,
    CRITICAL_FIELDS,
    critical_completeness,
    detect_duplicate_tickers,
    detect_missing_critical_fields,
    detect_missing_sector,
    detect_outliers,
    generate_data_quality_report,
    validate_dre_data,
    validate_macro_data,
    validate_multiples_data,
)

__all__ = [
    "CANONICAL_RANGES", "CRITICAL_FIELDS", "audit_multiples",
    "critical_completeness", "detect_duplicate_tickers",
    "detect_missing_critical_fields", "detect_missing_sector", "detect_outliers",
    "generate_data_quality_report", "validate_dre_data", "validate_macro_data",
    "validate_multiples_data",
]


def audit_multiples(df: pd.DataFrame) -> dict:
    """Relatório de auditoria de um DataFrame de múltiplos (uma linha por ticker)."""
    return generate_data_quality_report(df)
