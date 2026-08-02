"""Elegibilidade auditável da Metodologia Integrada de FIIs.

O módulo mantém os filtros do usuário separados do score. Um fundo reprovado
não recebe nota artificialmente menor: ele sai do universo elegível com razões
explícitas. Valores ausentes também não são convertidos em zero.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import math
from typing import Any, Iterable


INTEGRATED_MODEL_VERSION = "6.6.0"


@dataclass(frozen=True)
class IntegratedEligibilityPolicy:
    min_daily_liquidity: float = 1_000_000.0
    min_dy_12m: float = .08
    min_history_months: int = 24
    max_drawdown: float = .35
    pvp_min: float = .55
    pvp_max: float = 1.30
    require_pvp_below_one: bool = False
    require_multi_region: bool = False
    require_min_properties: bool = False
    min_properties: int = 8
    require_multicategory: bool = False


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _normalized_yield(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return number / 100.0 if number > 1 else number


def _eligibility_reasons(row: dict, policy: IntegratedEligibilityPolicy) -> list[str]:
    reasons: list[str] = []
    fii_type = str(row.get("tipo") or "").lower()
    liquidity = _number(row.get("liquidez_diaria"))
    dy = _normalized_yield(row.get("dy_12m"))
    pvp = _number(row.get("pvp"))
    history = _number(row.get("history_months"))
    drawdown = _number(row.get("max_drawdown"))

    if liquidity is None:
        reasons.append("liquidez ausente")
    elif liquidity < policy.min_daily_liquidity:
        reasons.append("liquidez abaixo do mínimo")
    if dy is None:
        reasons.append("DY 12m ausente")
    elif dy < policy.min_dy_12m:
        reasons.append("DY 12m abaixo do mínimo")
    elif dy > .20:
        reasons.append("DY 12m acima do limite de plausibilidade")
    if pvp is None:
        reasons.append("P/VP ausente")
    elif not policy.pvp_min <= pvp <= policy.pvp_max:
        reasons.append("P/VP fora da faixa de plausibilidade")
    elif policy.require_pvp_below_one and pvp >= 1:
        reasons.append("P/VP não está abaixo de 1")
    if policy.min_history_months > 0:
        if history is None:
            reasons.append("histórico ausente")
        elif history < policy.min_history_months:
            reasons.append("histórico abaixo do mínimo")
    if policy.max_drawdown > 0:
        if drawdown is None:
            reasons.append("drawdown ausente")
        elif drawdown < -policy.max_drawdown:
            reasons.append("drawdown acima da tolerância")

    if fii_type in {"tijolo", "hibrido"}:
        regions = _number(row.get("region_count"))
        properties = _number(row.get("property_count"))
        if policy.require_multi_region and (regions is None or regions < 2):
            reasons.append("menos de duas regiões identificadas")
        if policy.require_min_properties and (
            properties is None or properties < policy.min_properties
        ):
            reasons.append(f"menos de {policy.min_properties} imóveis identificados")
        if policy.require_multicategory and not bool(row.get("multi_category")):
            reasons.append("não classificado como multicategoria/híbrido")
    return reasons


def apply_integrated_eligibility(
    rows: Iterable[dict], policy: IntegratedEligibilityPolicy,
) -> tuple[list[dict], dict]:
    """Aplica filtros determinísticos e devolve razões agregadas de exclusão."""
    source = [dict(row) for row in rows]
    eligible: list[dict] = []
    exclusions: Counter[str] = Counter()
    for row in source:
        reasons = _eligibility_reasons(row, policy)
        enriched = {
            **row,
            "integrated_model_version": INTEGRATED_MODEL_VERSION,
            "eligibility_status": "eligible" if not reasons else "excluded",
            "eligibility_reasons": tuple(reasons),
        }
        if reasons:
            exclusions.update(reasons)
        else:
            eligible.append(enriched)
    report = {
        "model_version": INTEGRATED_MODEL_VERSION,
        "universe_count": len(source),
        "eligible_count": len(eligible),
        "eligible_fraction": len(eligible) / len(source) if source else 0.0,
        "exclusion_counts": dict(exclusions.most_common()),
        "policy": asdict(policy),
    }
    return eligible, report
