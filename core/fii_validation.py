"""Validação point-in-time e estatística da metodologia de FIIs v4."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ValidationThresholds:
    min_periods: int = 36
    min_universe_coverage: float = .80
    min_rank_stability: float = .45
    max_annual_turnover: float = 3.0
    min_regimes: int = 3


def bootstrap_mean_ci(values: Iterable[float], *, samples: int = 2000,
                      confidence: float = .95, seed: int = 42,
                      block_size: int | None = None) -> dict[str, float | int]:
    array = np.asarray([float(v) for v in values if pd.notna(v)], dtype=float)
    if array.size < 2:
        return {"n": int(array.size), "mean": float(array.mean()) if array.size else np.nan,
                "lower": np.nan, "upper": np.nan}
    rng = np.random.default_rng(seed)
    size = int(block_size or max(1, round(array.size ** (1 / 3))))
    size = min(size, array.size)
    if size == 1:
        means = rng.choice(array, size=(samples, array.size), replace=True).mean(axis=1)
    else:
        # Moving-block bootstrap circular preserva dependência serial local.
        starts = rng.integers(0, array.size, size=(samples, math.ceil(array.size / size)))
        offsets = np.arange(size)
        sampled = array[(starts[..., None] + offsets) % array.size].reshape(samples, -1)
        means = sampled[:, :array.size].mean(axis=1)
    alpha = (1 - confidence) / 2
    return {"n": int(array.size), "mean": float(array.mean()),
            "lower": float(np.quantile(means, alpha)), "upper": float(np.quantile(means, 1 - alpha))}


def ranking_stability(previous: pd.Series, current: pd.Series, *, top_k: int = 20) -> dict[str, float]:
    common = previous.dropna().index.intersection(current.dropna().index)
    spearman = float(previous.loc[common].corr(current.loc[common], method="spearman")) if len(common) >= 3 else np.nan
    prev_top = set(previous.nlargest(top_k).index)
    curr_top = set(current.nlargest(top_k).index)
    union = prev_top | curr_top
    return {"spearman": spearman, "top_k_jaccard": len(prev_top & curr_top) / len(union) if union else np.nan}


def portfolio_turnover(previous: pd.Series, current: pd.Series) -> float:
    index = previous.index.union(current.index)
    return float(.5 * (previous.reindex(index, fill_value=0) - current.reindex(index, fill_value=0)).abs().sum())


def point_in_time_backtest(
    snapshots: pd.DataFrame, returns: pd.DataFrame, benchmark: pd.Series, *,
    top_n: int = 12, transaction_cost: float = .0015, slippage: float = .0010,
) -> dict[str, Any]:
    """Backtest sem look-ahead usando apenas snapshots disponíveis na data.

    `snapshots` precisa conter reference_date, available_at, ticker, score e,
    idealmente, active_status. Fundos encerrados/incorporados permanecem no
    universo histórico; active_status só é avaliado na respectiva data.
    `returns` é long-form com date, ticker e total_return.
    """
    required_snap = {"reference_date", "available_at", "ticker", "score"}
    required_ret = {"date", "ticker", "total_return"}
    if not required_snap.issubset(snapshots.columns) or not required_ret.issubset(returns.columns):
        return {"status": "blocked", "blockers": ["colunas point-in-time obrigatórias ausentes"]}
    s = snapshots.copy()
    r = returns.copy()
    benchmark = benchmark.copy()
    benchmark.index = pd.to_datetime(benchmark.index).normalize()
    s["reference_date"] = pd.to_datetime(s["reference_date"]).dt.normalize()
    s["available_at"] = pd.to_datetime(s["available_at"], utc=True).dt.tz_localize(None)
    r["date"] = pd.to_datetime(r["date"]).dt.normalize()
    dates = sorted(set(s["reference_date"]))
    return_dates = sorted(set(r["date"]))
    previous = pd.Series(dtype=float)
    observations: list[dict] = []
    ranks: list[pd.Series] = []
    turnovers: list[float] = []
    for position, dt in enumerate(dates):
        decision_cutoff = dt + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        possible_execution = [value for value in return_dates if value > dt]
        if not possible_execution:
            continue
        execution_date = possible_execution[0]
        next_decision = dates[position + 1] if position + 1 < len(dates) else None
        next_execution = None
        if next_decision is not None:
            choices = [value for value in return_dates if value > next_decision]
            next_execution = choices[0] if choices else None
        universe = s[(s["reference_date"] <= dt) & (s["available_at"] <= decision_cutoff)]
        if "availability_quality" in universe:
            universe = universe[universe["availability_quality"] != "migration_baseline"]
        if "active_status" in universe:
            universe = universe[universe["active_status"].fillna("active").isin(["active", "listed"])]
        latest = universe.sort_values(["reference_date", "available_at"]).drop_duplicates(
            "ticker", keep="last")
        if latest.empty:
            continue
        scores = latest.set_index("ticker")["score"].astype(float)
        chosen = scores.nlargest(top_n).index
        weights = pd.Series(1 / len(chosen), index=chosen)
        period_rows = r[r["date"] >= execution_date]
        if next_execution is not None:
            period_rows = period_rows[period_rows["date"] < next_execution]
        period_matrix = period_rows.pivot_table(index="date", columns="ticker",
                                                values="total_return", aggfunc="last")
        valid = weights.index.intersection(period_matrix.columns)
        coverage = len(valid) / len(weights) if len(weights) else 0
        if not len(valid):
            continue
        weights = weights.loc[valid] / weights.loc[valid].sum()
        turn = portfolio_turnover(previous, weights) if not previous.empty else 1.0
        daily = period_matrix[valid].fillna(0.0).mul(weights, axis=1).sum(axis=1)
        gross = float((1.0 + daily).prod() - 1.0)
        net = gross - turn * (transaction_cost + slippage)
        benchmark_period = benchmark[(benchmark.index >= execution_date)]
        if next_execution is not None:
            benchmark_period = benchmark_period[benchmark_period.index < next_execution]
        benchmark_return = (float((1.0 + benchmark_period.dropna()).prod() - 1.0)
                            if len(benchmark_period.dropna()) else np.nan)
        observations.append({"date": execution_date, "decision_date": dt,
                             "portfolio_return": net,
                             "benchmark_return": benchmark_return,
                             "coverage": coverage, "turnover": turn})
        turnovers.append(turn)
        ranks.append(scores)
        previous = weights
    result = pd.DataFrame(observations)
    if result.empty:
        return {"status": "blocked", "blockers": ["nenhum período PIT elegível"]}
    excess = result["portfolio_return"] - result["benchmark_return"]
    stabilities = [ranking_stability(a, b)["spearman"] for a, b in zip(ranks, ranks[1:])]
    valid_stabilities = [value for value in stabilities if not np.isnan(value)]
    return {
        "status": "calculated", "periods": len(result),
        "mean_return": float(result["portfolio_return"].mean()),
        "mean_benchmark": float(result["benchmark_return"].mean()),
        "mean_excess": float(excess.mean()), "excess_bootstrap": bootstrap_mean_ci(excess),
        "mean_coverage": float(result["coverage"].mean()),
        "annualized_turnover": float(np.mean(turnovers) * 12),
        "rank_stability": float(np.mean(valid_stabilities)) if valid_stabilities else np.nan,
        "observations": result.to_dict("records"),
    }


def evaluate_regime_performance(observations: Iterable[dict], macro_regimes: pd.DataFrame) -> dict[str, Any]:
    """Separa excesso de retorno por regimes brasileiros previamente classificados.

    `macro_regimes` contém `date` e `regime` (por exemplo high_real_rate,
    easing, inflation_stress e stress). A classificação deve usar somente
    informações disponíveis na própria data.
    """
    returns = pd.DataFrame(list(observations))
    if returns.empty or not {"date", "portfolio_return", "benchmark_return"}.issubset(returns.columns):
        return {}
    if macro_regimes.empty or not {"date", "regime"}.issubset(macro_regimes.columns):
        return {}
    returns["date"] = pd.to_datetime(returns["date"])
    regimes = macro_regimes[["date", "regime"]].copy()
    regimes["date"] = pd.to_datetime(regimes["date"])
    merged = returns.merge(regimes, on="date", how="inner")
    output: dict[str, Any] = {}
    for regime, group in merged.groupby("regime"):
        excess = group["portfolio_return"] - group["benchmark_return"]
        output[str(regime)] = {
            "periods": len(group), "mean_return": float(group["portfolio_return"].mean()),
            "mean_excess": float(excess.mean()), "excess_bootstrap": bootstrap_mean_ci(excess),
            "worst_period": float(group["portfolio_return"].min()),
        }
    return output


def validate_methodology(backtest: dict[str, Any], regime_results: dict[str, Any], *,
                         thresholds: ValidationThresholds | None = None) -> dict[str, Any]:
    thresholds = thresholds or ValidationThresholds()
    blockers: list[str] = []
    if backtest.get("status") != "calculated":
        blockers.extend(backtest.get("blockers") or ["backtest PIT não calculado"])
    if int(backtest.get("periods") or 0) < thresholds.min_periods:
        blockers.append(f"histórico inferior a {thresholds.min_periods} períodos")
    if float(backtest.get("mean_coverage") or 0) < thresholds.min_universe_coverage:
        blockers.append("cobertura histórica insuficiente")
    stability = float(backtest.get("rank_stability") or 0)
    if pd.isna(stability) or stability < thresholds.min_rank_stability:
        blockers.append("ranking instável entre rebalanceamentos")
    if float(backtest.get("annualized_turnover") or np.inf) > thresholds.max_annual_turnover:
        blockers.append("turnover anual excessivo após custos")
    valid_regimes = sum(bool(value and value.get("periods")) for value in regime_results.values())
    if valid_regimes < thresholds.min_regimes:
        blockers.append("cobertura insuficiente de regimes macroeconômicos")
    ci = backtest.get("excess_bootstrap") or {}
    if pd.isna(ci.get("lower", np.nan)):
        blockers.append("intervalo bootstrap indisponível")
    return {"status": "passed" if not blockers else "blocked", "blockers": blockers,
            "thresholds": thresholds.__dict__, "backtest": backtest, "regimes": regime_results}
