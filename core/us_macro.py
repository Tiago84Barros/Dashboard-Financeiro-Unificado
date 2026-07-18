"""Regime macroeconômico dos EUA para a inteligência de Empresas Americanas.

O módulo é puro: recebe observações já disponíveis e devolve um diagnóstico
determinístico. A interface não consulta a rede; valores oficiais devem entrar
pelo pipeline/warehouse (FRED/BEA/BLS/Federal Reserve) ou por uma simulação
explicitamente identificada como tal.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import isfinite


@dataclass(frozen=True)
class USMacroSnapshot:
    fed_funds: float = 4.25
    cpi_yoy: float = 2.5
    real_gdp_yoy: float = 2.0
    unemployment: float = 4.2
    yield_curve_10y_2y: float = 0.25
    high_yield_spread: float = 3.5


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def evaluate_macro(snapshot: USMacroSnapshot | dict) -> dict:
    """Classifica o regime e calcula impulsos setoriais em escala -10..+10."""
    values = asdict(snapshot) if isinstance(snapshot, USMacroSnapshot) else dict(snapshot)
    clean = {}
    defaults = asdict(USMacroSnapshot())
    for key, default in defaults.items():
        try:
            value = float(values.get(key, default))
            clean[key] = value if isfinite(value) else default
        except (TypeError, ValueError):
            clean[key] = default

    inflation = _clip((3.0 - clean["cpi_yoy"]) * 1.8, -4, 4)
    growth = _clip((clean["real_gdp_yoy"] - 1.5) * 1.6, -4, 4)
    labor = _clip((4.8 - clean["unemployment"]) * 1.2, -3, 3)
    curve = _clip(clean["yield_curve_10y_2y"] * 1.5, -3, 3)
    credit = _clip((4.5 - clean["high_yield_spread"]) * 1.1, -4, 4)
    rates = _clip((4.0 - clean["fed_funds"]) * 1.2, -4, 4)
    score = round(_clip(50 + 2.0 * (inflation + growth + labor + curve + credit + rates), 0, 100), 1)

    if score >= 65:
        regime, tone = "Expansão / apetite a risco", "favorável"
    elif score >= 45:
        regime, tone = "Transição / neutro", "neutro"
    else:
        regime, tone = "Desaceleração / aversão a risco", "adverso"

    sector_impacts = {
        "Technology": _clip(growth + rates + credit, -10, 10),
        "Communication Services": _clip(growth + rates, -10, 10),
        "Consumer Cyclical": _clip(growth + labor + credit, -10, 10),
        "Financial Services": _clip(curve + growth + credit - max(rates, 0), -10, 10),
        "Real Estate": _clip(1.5 * rates + credit, -10, 10),
        "Industrials": _clip(growth + labor, -10, 10),
        "Energy": _clip(growth + inflation * -0.4, -10, 10),
        "Healthcare": _clip(1.5 - abs(growth) * 0.2, -10, 10),
        "Consumer Defensive": _clip(1.5 - growth * 0.2, -10, 10),
        "Utilities": _clip(rates + 1.0, -10, 10),
    }
    return {
        "score": score,
        "regime": regime,
        "tone": tone,
        "inputs": clean,
        "drivers": {
            "Inflação": round(inflation, 2), "Crescimento": round(growth, 2),
            "Mercado de trabalho": round(labor, 2), "Curva de juros": round(curve, 2),
            "Crédito": round(credit, 2), "Política monetária": round(rates, 2),
        },
        "sector_impacts": {k: round(v, 2) for k, v in sector_impacts.items()},
    }
