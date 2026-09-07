"""Relações explícitas e auditáveis entre fator macro e ativo."""

from __future__ import annotations

from dataclasses import dataclass

from core.macro_data.signals import MacroSignal


@dataclass(frozen=True)
class AssetMacroExposure:
    asset_id: str
    factor: str
    sensitivity: float  # -1..+1; não é inferida por texto.
    confidence: float  # 0..1, qualidade do mapeamento.
    channel: str


@dataclass(frozen=True)
class AssetMacroImpact:
    asset_id: str
    direction: str
    intensity: float
    confidence: float
    channel: str | None


def assess_asset_impact(
    signal: MacroSignal, exposures: list[AssetMacroExposure], *, factor: str
) -> tuple[AssetMacroImpact, ...]:
    direction = (
        1
        if signal.direction == "favorable"
        else -1
        if signal.direction == "adverse"
        else 0
    )
    impacts = []
    for exposure in (e for e in exposures if e.factor == factor):
        if not -1 <= exposure.sensitivity <= 1 or not 0 <= exposure.confidence <= 1:
            raise ValueError("sensibilidade/confiança de exposição fora do intervalo")
        effect = direction * exposure.sensitivity
        impacts.append(
            AssetMacroImpact(
                exposure.asset_id,
                "positive" if effect > 0 else "negative" if effect < 0 else "neutral",
                round(abs(effect) * signal.impact_score, 2),
                round(exposure.confidence * signal.confidence_score, 2),
                exposure.channel,
            )
        )
    return tuple(impacts)
