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
    """Limites da camada macro, dimensionados por medição e não por intuição.

    ``max_score_adjustment`` é **inclinação, não teto**
    ---------------------------------------------------
    O ``clip(±max_score_adjustment)`` em :func:`apply_macro_scores` só morde
    quando ``|impacto| * escala >= 100`` -- ou seja, ``|impacto| >= 67`` no modo
    ``scenario``. O maior ``|impacto|`` observado em 188 cortes mensais
    (2011-2026, ``scripts/backtest_macro_tilt.py``) foi **31,3**. O corte nunca
    aconteceu e não vai acontecer: o que este número faz na prática é
    multiplicar o impacto, não limitá-lo.

    Por que 4,0, e não os 10,0 originais
    ------------------------------------
    Este parâmetro **não é validável contra desfecho**. Não existe série
    histórica de notas fundamentalistas para medir se a nota ajustada acerta
    mais que a nota crua, e o backtest não achou Rank-IC em classe nenhuma:
    t_NW entre -0,21 (US, 1m) e +1,07 (FII, 1m), com o horizonte de 12 meses dos
    EUA em -1,61. O efeito na carteira fica em +0,01%/ano. Sem poder discriminar
    por acerto, o único critério honesto é **tamanho de efeito**: quanto de
    reordenação um sinal não validado pode causar.

    A régua adotada é declarada e verificável -- *no seu extremo observado, a
    camada macro não pode mover um nome mais de um decil da tabela de notas*.
    Medido contra a safra ``0.8.0 @ 2025-06-30`` (2 443 notas, mediana 51,70):

    ======  ===================  =========================
      M     ajuste alcançável    posições deslocadas
    ======  ===================  =========================
     10,0          4,70 pt              21,6%
      5,0          2,35 pt              11,2%
      **4,0**      **1,88 pt**          **9,3%**
      3,0          1,41 pt               7,2%
    ======  ===================  =========================

    4,0 é o maior valor que mantém o extremo observado dentro de um decil
    (244 nomes). Ele **não** foi escolhido por prever melhor -- não há evidência
    disso -- e sim por limitar o estrago de um sinal que ainda não se provou.

    ``max_relative_weight_tilt`` fica em 0,15 porque nunca mordeu: com
    ``|impacto|`` máximo de 31,3 o peso relativo se move no máximo 4,7%.
    Apertá-lo não mudaria uma carteira sequer, e um limite que não morde não
    ganha rigor por ser reescrito.
    """

    max_score_adjustment: float = 4.0
    max_relative_weight_tilt: float = 0.15
    max_turnover: float = 0.10


def bound_macro_weights(base: pd.Series, proposed: pd.Series,
                        config: MacroTiltConfig = MacroTiltConfig()) -> pd.Series:
    """Segmento convexo entre duas carteiras factíveis preserva seus tetos lineares.

    Limita 0,5*sum(abs(delta)) e abs(delta_i)/base_i após TODAS as projeções.
    Uma posição com peso-base zero não pode ser criada pelo overlay macro.
    """
    if not base.index.equals(proposed.index):
        raise ValueError("pesos macro desalinhados")
    for weights in (base, proposed):
        if not np.isfinite(weights).all() or (weights < 0).any() or not np.isclose(weights.sum(), 1):
            raise ValueError("pesos inválidos para limite macro")
    if config.max_relative_weight_tilt < 0 or config.max_turnover < 0:
        raise ValueError("limites macro negativos")
    delta = proposed - base
    ratios = (base * config.max_relative_weight_tilt / delta.abs()).where(delta.abs() > 1e-14, 1)
    turnover = float(.5 * delta.abs().sum())
    alpha = min(1.0, float(ratios.min()), config.max_turnover / turnover if turnover else 1.0)
    return base + max(alpha, 0.0) * delta


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
    raw = pd.to_numeric(raw, errors="coerce").replace([np.inf, -np.inf], np.nan)
    result["macro_covered"] = raw.notna()
    result["macro_impact"] = raw
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
    if not np.isfinite(weights).all() or (weights < 0).any() or float(weights.sum()) <= 0:
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
    result["weight"] = bound_macro_weights(weights, proposed / float(proposed.sum()), config)
    result.attrs["macro_mode"] = mode
    result.attrs["macro_coverage"] = float(result["macro_covered"].mean())
    result.attrs["macro_turnover"] = float(0.5 * np.abs(result["weight"] - weights).sum())
    return result
