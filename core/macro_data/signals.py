"""Sinais macro explicáveis; não consulta rede nem emite ordem de investimento."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean, stdev
from typing import Sequence

from core.macro_data.models import MacroObservation


@dataclass(frozen=True)
class MacroSignal:
    direction: str
    impact_score: float
    confidence_score: float
    urgency_score: float
    relevance_score: float
    data_quality_score: float
    classification: str
    decomposition: dict[str, float]
    limitations: tuple[str, ...] = ()


def evaluate_observation(
    observations: Sequence[MacroObservation],
    *,
    desirability: int,
    importance: float = 0.5,
    surprise: float | None = None,
    z_window: int = 24,
) -> MacroSignal:
    values = [
        float(o.value)
        for o in observations
        if o.value is not None and isfinite(float(o.value))
    ]
    if len(values) < 2:
        return MacroSignal(
            "unknown",
            0,
            0,
            0,
            importance * 100,
            0,
            "informativo",
            {},
            ("histórico insuficiente",),
        )
    latest, previous = values[-1], values[-2]
    change = latest - previous
    window = values[-z_window:]
    sigma = stdev(window) if len(window) > 1 else 0.0
    zscore = (latest - mean(window)) / sigma if sigma else 0.0
    magnitude = min(abs(zscore) / 3, 1.0)
    surprise_score = min(abs(surprise or 0.0) / 3, 1.0)
    freshness = 1.0  # o repositório aplica a idade real na consulta contextual.
    impact = min(
        100.0, 100 * (0.55 * magnitude + 0.25 * surprise_score + 0.20 * importance)
    )
    confidence = min(
        100.0, 100 * (0.45 + 0.35 * min(len(values) / z_window, 1) + 0.20 * freshness)
    )
    urgency = min(100.0, impact * (0.5 + 0.5 * surprise_score))
    signed = change * (1 if desirability >= 0 else -1)
    direction = "favorable" if signed > 0 else "adverse" if signed < 0 else "neutral"
    label = (
        "crítico"
        if impact >= 90 and confidence >= 75
        else "alto impacto"
        if impact >= 70
        else "relevante"
        if impact >= 45
        else "atenção"
        if impact >= 20
        else "informativo"
    )
    return MacroSignal(
        direction,
        round(impact, 2),
        round(confidence, 2),
        round(urgency, 2),
        round(importance * 100, 2),
        100.0,
        label,
        {
            "magnitude": round(magnitude, 4),
            "surprise": round(surprise_score, 4),
            "importance": importance,
            "z_score": round(zscore, 4),
        },
    )
