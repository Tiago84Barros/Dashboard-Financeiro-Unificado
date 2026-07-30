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


def _optional_num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _first_number(*values: Any) -> float | None:
    return next((number for value in values if (number := _optional_num(value)) is not None), None)


def scenario_missing_input_penalty(row: dict, scenario: str) -> tuple[float, tuple[str, ...]]:
    """Penaliza desconhecimento sem inventar vacância, crédito ou duration zero."""
    missing: list[str] = []
    penalty = 0.0

    def require(name: str, value: Any, cost: float) -> None:
        nonlocal penalty
        if _optional_num(value) is None:
            missing.append(name)
            penalty += cost

    if scenario == "selic_alta":
        require("duration_anos", row.get("duration_anos"), .02)
        require("leverage", row.get("leverage"), .02)
    elif scenario == "queda_selic":
        require("duration_anos", row.get("duration_anos"), .01)
        require("pvp", row.get("pvp"), .01)
    elif scenario == "inflacao_alta":
        indexers = row.get("indexers")
        if not isinstance(indexers, dict) or not indexers:
            missing.append("indexers")
            penalty += .02
        require("leverage", row.get("leverage"), .02)
    elif scenario == "vacancia":
        if _first_number(row.get("vacancia_financeira"), row.get("vacancia_fisica")) is None:
            missing.append("vacancia")
            penalty += .06
        concentration = _first_number(
            row.get("tenant_concentration"), row.get("issuance_concentration"),
            row.get("holdings_overlap"),
        )
        if concentration is None:
            missing.append("concentracao")
            penalty += .03
        require("leverage", row.get("leverage"), .02)
    elif scenario == "credito":
        require("delinquency", row.get("delinquency"), .06)
        require("ltv", row.get("ltv"), .03)
        concentration = _first_number(
            row.get("issuance_concentration"), row.get("tenant_concentration"),
            row.get("holdings_overlap"),
        )
        if concentration is None:
            missing.append("concentracao")
            penalty += .03
    return penalty, tuple(missing)


def asset_scenario_return(row: dict, scenario: str) -> float:
    """Retorno de estresse do tipo ajustado às exposições observadas do fundo."""
    fii_type = str(row.get("tipo") or "hibrido").lower()
    base = float(type_scenario_return(fii_type, scenario))
    confidence = min(max(_num(row.get("confidence"), .0), 0.0), 1.0)
    leverage = min(max(_num(row.get("leverage"), 0.0), 0.0), 1.5)
    vacancy = min(max(_first_number(row.get("vacancia_financeira"),
                                    row.get("vacancia_fisica")) or 0.0, 0.0), 1.0)
    delinquency = min(max(_optional_num(row.get("delinquency")) or 0.0, 0.0), 1.0)
    ltv = min(max(_optional_num(row.get("ltv")) or 0.0, 0.0), 1.5)
    duration = min(max(_optional_num(row.get("duration_anos")) or 0.0, 0.0), 20.0)
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
    missing_penalty, _ = scenario_missing_input_penalty(row, scenario)
    return float(base + adjustment - ambiguity - missing_penalty)


def scenario_matrix(rows: list[dict], scenarios: tuple[str, ...]) -> list[list[float]]:
    return [[asset_scenario_return(row, scenario) for scenario in scenarios] for row in rows]
