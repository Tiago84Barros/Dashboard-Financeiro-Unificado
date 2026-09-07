"""Supressão conservadora de alertas macro."""

from __future__ import annotations

from dataclasses import dataclass

from core.macro_data.signals import MacroSignal


@dataclass(frozen=True)
class MacroAlert:
    level: str
    published: bool
    reason: str
    impact_score: float
    confidence_score: float


def classify_alert(
    signal: MacroSignal, *, independent_confirmations: int = 0
) -> MacroAlert:
    if signal.impact_score < 45 or signal.confidence_score < 50:
        return MacroAlert(
            "informativo",
            False,
            "variação sem magnitude ou confiança suficiente",
            signal.impact_score,
            signal.confidence_score,
        )
    if (
        signal.impact_score >= 90
        and signal.confidence_score >= 75
        and independent_confirmations >= 2
    ):
        return MacroAlert(
            "crítico",
            True,
            "magnitude excepcional confirmada por fontes independentes",
            signal.impact_score,
            signal.confidence_score,
        )
    return MacroAlert(
        "alto impacto" if signal.impact_score >= 70 else "relevante",
        True,
        "limiares de impacto e confiança atendidos",
        signal.impact_score,
        signal.confidence_score,
    )
