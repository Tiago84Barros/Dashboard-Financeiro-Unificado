"""Calibração temporal dos parâmetros de alocação.

O score cross-sectional é tratado como entrada. Os parâmetros de transformação
do score em pesos são escolhidos apenas pelo desempenho agregado em blocos
futuros, separados do histórico anterior por um gap temporal.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd

from core.portfolio_constraints import (
    InfeasiblePortfolioConstraint,
    project_capped_simplex,
)


def _score_weights(
    tickers: list[str],
    score_map: Mapping[str, float],
    gamma: float,
    cap: float,
    soft: float,
) -> dict[str, float]:
    scores = np.asarray([float(score_map.get(t, 0.0) or 0.0) for t in tickers])
    scores[~np.isfinite(scores)] = 0.0
    shifted = np.maximum(scores - scores.min(), 0.0) + 1e-6
    raw = shifted ** float(gamma)
    raw = raw / raw.sum()
    tilted = {
        ticker: float(value + (value - soft) * 0.5 if value > soft else value)
        for ticker, value in zip(tickers, raw)
    }
    return project_capped_simplex(tilted, cap)


def _holdout_objective(
    prices_with_anchor: pd.DataFrame,
    weights: Mapping[str, float],
) -> float | None:
    """Objetivo somente no holdout; a primeira linha serve apenas de âncora."""
    if len(prices_with_anchor) < 3:
        return None
    returns = prices_with_anchor[list(weights)].pct_change(fill_method=None).iloc[1:]
    portfolio_returns: list[float] = []
    for _, row in returns.iterrows():
        available = [
            ticker for ticker in weights
            if pd.notna(row.get(ticker)) and np.isfinite(float(row[ticker]))
        ]
        if not available:
            continue
        total_weight = sum(float(weights[t]) for t in available)
        portfolio_returns.append(
            sum(float(row[t]) * float(weights[t]) for t in available) / total_weight
        )
    if len(portfolio_returns) < 2:
        return None

    series = pd.Series(portfolio_returns, dtype=float).clip(lower=-0.999999)
    growth = float((1.0 + series).prod())
    annual_return = growth ** (12.0 / len(series)) - 1.0
    annual_vol = float(series.std(ddof=1)) * np.sqrt(12.0)
    wealth = (1.0 + series).cumprod()
    drawdown = float(abs(((wealth / wealth.cummax()) - 1.0).min()))
    return annual_return - 0.60 * annual_vol - 0.40 * drawdown


def purged_walk_forward_calibration(
    prices: pd.DataFrame,
    score_map: Mapping[str, float],
    tickers: Iterable[str],
    *,
    gamma_grid: Iterable[float],
    cap_grid: Iterable[float],
    soft_grid: Iterable[float],
    defaults: tuple[float, float, float],
    n_folds: int = 4,
    min_train_months: int = 24,
    purge_months: int = 3,
    embargo_months: int = 2,
    shrinkage: float = 0.40,
) -> tuple[tuple[float, float, float], dict]:
    """Seleciona parâmetros pelo desempenho exclusivamente fora da amostra.

    Cada fold usa:

    ``histórico anterior | purge | holdout futuro | embargo``

    O holdout nunca é incluído no cálculo do objetivo daquele fold. O próximo
    fold pode incorporá-lo ao histórico anterior, como em walk-forward real.
    """
    defaults = tuple(float(v) for v in defaults)
    if prices.empty:
        return defaults, {"folds": 0, "reason": "empty_prices"}

    candidates = [
        str(t) for t in tickers
        if str(t) in prices.columns and str(t) in score_map
    ][:5]
    if len(candidates) < 2:
        return defaults, {"folds": 0, "reason": "insufficient_assets"}

    clean = prices[candidates].sort_index().copy()
    n_total = len(clean)
    gap = max(int(purge_months), 0)
    embargo = max(int(embargo_months), 0)
    min_train = max(int(min_train_months), 6)
    available = n_total - min_train - gap
    if available < 6:
        return defaults, {"folds": 0, "reason": "insufficient_history"}

    test_size = max(6, available // max(int(n_folds), 1))
    folds: list[tuple[int, int, int]] = []
    train_end = min_train
    while len(folds) < max(int(n_folds), 1):
        test_start = train_end + gap
        test_end = min(test_start + test_size, n_total)
        if test_end - test_start < 3:
            break
        folds.append((train_end, test_start, test_end))
        train_end = test_end + embargo
        if train_end + gap + 3 > n_total:
            break

    if not folds:
        return defaults, {"folds": 0, "reason": "no_valid_fold"}

    objectives: dict[tuple[float, float, float], list[float]] = {}
    for gamma in gamma_grid:
        for cap in cap_grid:
            for soft in soft_grid:
                params = (float(gamma), float(cap), float(soft))
                try:
                    weights = _score_weights(
                        candidates, score_map, params[0], params[1], params[2]
                    )
                except InfeasiblePortfolioConstraint:
                    continue
                scores: list[float] = []
                for train_end, test_start, test_end in folds:
                    # A linha imediatamente anterior ao holdout é somente a
                    # âncora para calcular o primeiro retorno do bloco futuro.
                    anchor = max(test_start - 1, train_end)
                    objective = _holdout_objective(
                        clean.iloc[anchor:test_end],
                        weights,
                    )
                    if objective is not None and np.isfinite(objective):
                        scores.append(float(objective))
                if scores:
                    objectives[params] = scores

    if not objectives:
        return defaults, {"folds": len(folds), "reason": "no_feasible_candidate"}

    best = max(
        objectives,
        key=lambda params: (
            float(np.median(objectives[params])),
            -float(np.std(objectives[params])),
        ),
    )
    final = tuple(
        round(best[i] * (1.0 - shrinkage) + defaults[i] * shrinkage, 3)
        for i in range(3)
    )
    return final, {
        "folds": len(folds),
        "test_size_months": test_size,
        "purge_months": gap,
        "embargo_months": embargo,
        "best_raw": best,
        "median_oos_objective": float(np.median(objectives[best])),
        "assets": candidates,
    }
