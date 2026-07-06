"""Nested walk-forward para parâmetros de transformação score → pesos.

Cada fold escolhe os hiperparâmetros somente no histórico anterior e mede a
escolha no bloco futuro. O último bloco é reservado como auditoria final e não
participa da escolha dos parâmetros devolvidos.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd

from core.portfolio_constraints import (
    InfeasiblePortfolioConstraint,
    project_capped_simplex,
)


Params = tuple[float, float, float]


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


def _normalized_schedule(
    score_history: Mapping[object, Mapping[str, float]],
) -> list[tuple[pd.Timestamp, Mapping[str, float]]]:
    schedule = []
    for date, scores in score_history.items():
        ts = pd.Timestamp(date)
        if ts.tzinfo is not None:
            ts = ts.tz_convert(None)
        schedule.append((ts, scores))
    return sorted(schedule, key=lambda item: item[0])


def _dynamic_objective(
    prices_with_anchor: pd.DataFrame,
    score_history: Mapping[object, Mapping[str, float]],
    params: Params,
    cost_cfg=None,
) -> float | None:
    """Objetivo time-weighted com scores conhecidos em cada data."""
    if len(prices_with_anchor) < 3 or not score_history:
        return None
    schedule = _normalized_schedule(score_history)
    returns = prices_with_anchor.pct_change(fill_method=None).iloc[1:]
    portfolio_returns: list[float] = []

    schedule_idx = -1
    weights: dict[str, float] = {}
    rebalance_cost = 0.0
    for date, row in returns.iterrows():
        date_ts = pd.Timestamp(date)
        if date_ts.tzinfo is not None:
            date_ts = date_ts.tz_convert(None)
        while (
            schedule_idx + 1 < len(schedule)
            and schedule[schedule_idx + 1][0] <= date_ts
        ):
            schedule_idx += 1
            score_map = schedule[schedule_idx][1]
            eligible = [
                ticker for ticker in prices_with_anchor.columns
                if ticker in score_map and np.isfinite(float(score_map[ticker]))
            ]
            try:
                new_weights = _score_weights(eligible, score_map, *params)
                if cost_cfg is not None and getattr(cost_cfg, "ativo", False):
                    from core.transaction_costs import is_large_cap
                    all_tickers = set(weights) | set(new_weights)
                    rebalance_cost = sum(
                        abs(new_weights.get(ticker, 0.0) - weights.get(ticker, 0.0))
                        * (
                            cost_cfg.spread_bps_large
                            if is_large_cap(ticker) else cost_cfg.spread_bps_small
                        )
                        / 2.0 / 10_000.0
                        for ticker in all_tickers
                    )
                weights = new_weights
            except (InfeasiblePortfolioConstraint, ValueError):
                weights = {}
        if not weights:
            continue
        available = [
            ticker for ticker in weights
            if pd.notna(row.get(ticker)) and np.isfinite(float(row[ticker]))
        ]
        if not available:
            continue
        total_weight = sum(float(weights[t]) for t in available)
        portfolio_returns.append(
            sum(float(row[t]) * float(weights[t]) for t in available) / total_weight
            - rebalance_cost
        )
        rebalance_cost = 0.0

    if len(portfolio_returns) < 3:
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
    score_history: Mapping[object, Mapping[str, float]],
    tickers: Iterable[str],
    *,
    gamma_grid: Iterable[float],
    cap_grid: Iterable[float],
    soft_grid: Iterable[float],
    defaults: Params,
    n_folds: int = 4,
    min_train_months: int = 24,
    purge_months: int = 3,
    embargo_months: int = 2,
    shrinkage: float = 0.40,
    cost_cfg=None,
) -> tuple[Params, dict]:
    """Nested walk-forward com último fold intocado para auditoria."""
    defaults = tuple(float(v) for v in defaults)
    if prices.empty or not score_history:
        return defaults, {"folds": 0, "reason": "missing_prices_or_score_history"}

    candidates = [str(t) for t in tickers if str(t) in prices.columns]
    if len(candidates) < 2:
        return defaults, {"folds": 0, "reason": "insufficient_assets"}
    clean = prices[candidates].sort_index().copy()

    n_total = len(clean)
    gap = max(int(purge_months), 0)
    embargo = max(int(embargo_months), 0)
    min_train = max(int(min_train_months), 12)
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
    if len(folds) < 2:
        return defaults, {"folds": len(folds), "reason": "need_two_nested_folds"}

    grid: list[Params] = [
        (float(g), float(c), float(s))
        for g in gamma_grid for c in cap_grid for s in soft_grid
    ]
    fold_results: list[dict] = []
    for train_end, test_start, test_end in folds:
        train_slice = clean.iloc[:train_end]
        inner_scores: dict[Params, float] = {}
        for params in grid:
            objective = _dynamic_objective(
                train_slice, score_history, params, cost_cfg=cost_cfg
            )
            if objective is not None and np.isfinite(objective):
                inner_scores[params] = float(objective)
        if not inner_scores:
            continue
        selected = max(inner_scores, key=inner_scores.get)
        anchor = max(test_start - 1, train_end)
        outer_objective = _dynamic_objective(
            clean.iloc[anchor:test_end],
            score_history,
            selected,
            cost_cfg=cost_cfg,
        )
        fold_results.append({
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "selected": selected,
            "inner_objective": inner_scores[selected],
            "outer_objective": outer_objective,
        })

    if len(fold_results) < 2:
        return defaults, {"folds": len(fold_results), "reason": "insufficient_valid_folds"}

    # O último fold é auditoria final: não participa da escolha.
    development = fold_results[:-1]
    final_audit = fold_results[-1]
    counts = Counter(result["selected"] for result in development)
    winner = max(
        counts,
        key=lambda params: (
            counts[params],
            np.median([
                result["inner_objective"]
                for result in development if result["selected"] == params
            ]),
        ),
    )
    final = tuple(
        round(winner[i] * (1.0 - shrinkage) + defaults[i] * shrinkage, 3)
        for i in range(3)
    )
    return final, {
        "folds": len(fold_results),
        "development_folds": len(development),
        "test_size_months": test_size,
        "purge_months": gap,
        "embargo_months": embargo,
        "winner_raw": winner,
        "final_audit_selected": final_audit["selected"],
        "final_audit_objective": final_audit["outer_objective"],
        "final_audit_start": str(clean.index[final_audit["test_start"]].date()),
        "assets": candidates,
        "costs_enabled": bool(cost_cfg is not None and getattr(cost_cfg, "ativo", False)),
    }
