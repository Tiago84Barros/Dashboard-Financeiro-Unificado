"""Choques específicos por ativo para otimização robusta de FIIs brasileiros."""
from __future__ import annotations

import math
from typing import Any

from core.fii_methodology import type_scenario_return


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def asset_scenario_return(row: dict, scenario: str) -> float:
    """Retorno de estresse do tipo ajustado às exposições observadas do fundo."""
    fii_type = str(row.get("tipo") or "hibrido").lower()
    base = float(type_scenario_return(fii_type, scenario))
    confidence = min(max(_num(row.get("confidence"), .0), 0.0), 1.0)
    leverage = min(max(_num(row.get("leverage"), 0.0), 0.0), 1.5)
    vacancy = min(max(_num(row.get("vacancia_financeira") or row.get("vacancia_fisica"), 0.0), 0.0), 1.0)
    delinquency = min(max(_num(row.get("delinquency"), 0.0), 0.0), 1.0)
    ltv = min(max(_num(row.get("ltv"), 0.0), 0.0), 1.5)
    duration = min(max(_num(row.get("duration_anos"), 0.0), 0.0), 20.0)
    concentration = max(
        _num(row.get("tenant_concentration")),
        _num(row.get("issuance_concentration")),
        _num(row.get("holdings_overlap")),
    )
    adjustment = 0.0
    if scenario == "selic_alta":
        adjustment -= .006 * duration
        adjustment -= .025 * leverage
    elif scenario == "queda_selic":
        adjustment += .003 * duration
        adjustment += .015 * min(max(1.0 - _num(row.get("pvp"), 1.0), 0.0), .5)
    elif scenario == "inflacao_alta":
        ipca_weight = _num((row.get("indexers") or {}).get("IPCA")) if isinstance(row.get("indexers"), dict) else 0.0
        adjustment += .03 * ipca_weight - .015 * leverage
    elif scenario == "vacancia":
        adjustment -= .30 * vacancy + .08 * concentration + .03 * leverage
    elif scenario == "credito":
        adjustment -= .45 * delinquency + .08 * max(ltv - .60, 0.0) + .10 * concentration
    # Dados incertos ampliam a perda, mas não criam retorno positivo artificial.
    ambiguity = .04 * (1.0 - confidence)
    return float(base + adjustment - ambiguity)


def scenario_matrix(rows: list[dict], scenarios: tuple[str, ...]) -> list[list[float]]:
    return [[asset_scenario_return(row, scenario) for scenario in scenarios] for row in rows]
