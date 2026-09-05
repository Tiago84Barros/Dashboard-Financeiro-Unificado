"""Tilt macro moderado e determinístico para scores e pesos de carteira.

O motor recebe impactos já calculados e alinhados à data da análise. Ausência de
impacto mantém o peso original e é marcada como falta de cobertura, não como zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MacroTiltConfig:
    max_score_adjustment: float = 10.0
    max_relative_weight_tilt: float = 0.15
    max_turnover: float = 0.10


def apply_macro_scores(
    frame: pd.DataFrame,
    impacts: Mapping[str, float],
    *,
    symbol_column: str,
    score_column: str,
    mode: str = "moderate",
    config: MacroTiltConfig = MacroTiltConfig(),
) -> pd.DataFrame:
    """Anota score fundamental e contextual sem criar ou alterar pesos."""
    if frame is None or frame.empty:
        return pd.DataFrame() if frame is None else frame.copy()
    if mode not in {"fundamental", "moderate", "scenario"}:
        raise ValueError("modo macro inválido")
    result = frame.copy()
    raw = result[symbol_column].astype(str).map(impacts)
    result["macro_covered"] = raw.notna()
    result["macro_impact"] = pd.to_numeric(raw, errors="coerce")
    capped = result["macro_impact"].clip(-100, 100)
    scale = 0.0 if mode == "fundamental" else (1.0 if mode == "moderate" else 1.5)
    adjustment = (capped / 100 * config.max_score_adjustment * scale).clip(
        -config.max_score_adjustment, config.max_score_adjustment
    )
    result["macro_score_adjustment"] = adjustment
    result["contextual_score"] = (
        pd.to_numeric(result[score_column], errors="coerce") + adjustment.fillna(0)
    )
    return result


def apply_macro_tilt(
    holdings: pd.DataFrame,
    impacts: Mapping[str, float],
    *,
    symbol_column: str,
    score_column: str,
    mode: str = "moderate",
    config: MacroTiltConfig = MacroTiltConfig(),
) -> pd.DataFrame:
    """Aplica ajuste limitado; pesos permanecem positivos e somam 1.

    ``impacts`` usa pontos no intervalo [-100, 100]. O ajuste do score é
    limitado a ``max_score_adjustment`` e o turnover é 0,5*Σ|w_novo-w_atual|.
    """
    if holdings is None or holdings.empty:
        return pd.DataFrame() if holdings is None else holdings.copy()
    if mode not in {"fundamental", "moderate", "scenario"}:
        raise ValueError("modo macro inválido")
    result = holdings.copy()
    weights = pd.to_numeric(result["weight"], errors="coerce")
    if weights.isna().any() or (weights < 0).any() or float(weights.sum()) <= 0:
        raise ValueError("pesos inválidos para tilt macro")
    weights = weights / float(weights.sum())
    result = apply_macro_scores(
        result, impacts, symbol_column=symbol_column,
        score_column=score_column, mode=mode, config=config,
    )
    capped = result["macro_impact"].clip(-100, 100)
    scale = 0.0 if mode == "fundamental" else (1.0 if mode == "moderate" else 1.5)
    multipliers = 1 + (capped.fillna(0) / 100 * config.max_relative_weight_tilt * scale)
    proposed = weights * multipliers.clip(lower=0)
    proposed = proposed / float(proposed.sum())
    turnover = float(0.5 * np.abs(proposed - weights).sum())
    if turnover > config.max_turnover and turnover > 0:
        proposed = weights + (proposed - weights) * (config.max_turnover / turnover)
    result["weight_before_macro"] = weights
    result["weight"] = proposed / float(proposed.sum())
    result.attrs["macro_mode"] = mode
    result.attrs["macro_coverage"] = float(result["macro_covered"].mean())
    result.attrs["macro_turnover"] = float(0.5 * np.abs(result["weight"] - weights).sum())
    return result
