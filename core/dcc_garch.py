"""
core/dcc_garch.py - Dynamic Conditional Correlation GARCH.

Implements a compact Engle (2002) DCC-GARCH workflow without external
optimizer dependencies:

1. Fit univariate GARCH(1,1) volatilities by Gaussian QMLE grid search.
2. Standardize returns by conditional volatilities.
3. Fit DCC(1,1) parameters by grid search on the correlation likelihood.
4. Produce the full time-varying correlation cube and latest correlation.

This is intentionally conservative: small grids keep Streamlit responsive and
fall back to stable parameters when data is thin.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


EPS = 1e-10


@dataclass(frozen=True)
class UnivariateGarchFit:
    omega: float
    alpha: float
    beta: float
    variance: np.ndarray
    neg_loglik: float


@dataclass(frozen=True)
class DCCGarchResult:
    tickers: tuple[str, ...]
    alpha: float
    beta: float
    unconditional_corr: np.ndarray
    correlations: np.ndarray
    volatilities: np.ndarray
    standardized_returns: np.ndarray
    neg_loglik: float

    @property
    def latest_corr(self) -> np.ndarray:
        return self.correlations[-1]

    @property
    def average_pairwise_latest(self) -> float:
        return average_pairwise_correlation(self.latest_corr)


def _to_returns_frame(prices_or_returns: pd.DataFrame, is_prices: bool = True) -> pd.DataFrame:
    df = prices_or_returns.copy()
    df = df.apply(pd.to_numeric, errors="coerce")
    if is_prices:
        df = df.pct_change()
    df = df.replace([np.inf, -np.inf], np.nan).dropna(how="all")
    keep = [c for c in df.columns if df[c].notna().sum() >= 24]
    return df[keep].dropna(axis=0, how="any") if keep else pd.DataFrame()


def _as_array(returns: pd.DataFrame | np.ndarray) -> tuple[np.ndarray, tuple[str, ...]]:
    if isinstance(returns, pd.DataFrame):
        cols = tuple(str(c) for c in returns.columns)
        arr = returns.to_numpy(dtype=float)
    else:
        arr = np.asarray(returns, dtype=float)
        cols = tuple(f"A{i+1}" for i in range(arr.shape[1] if arr.ndim == 2 else 0))
    if arr.ndim != 2 or arr.shape[0] < 12 or arr.shape[1] < 2:
        raise ValueError("returns must be (T, N) with T >= 12 and N >= 2")
    if not np.all(np.isfinite(arr)):
        mask = np.isfinite(arr).all(axis=1)
        arr = arr[mask]
    if arr.shape[0] < 12:
        raise ValueError("not enough finite observations")
    return arr, cols


def _garch_variance_path(r: np.ndarray, omega: float, alpha: float, beta: float) -> np.ndarray:
    h = np.empty_like(r, dtype=float)
    base_var = float(np.nanvar(r, ddof=1))
    base_var = max(base_var, EPS)
    h[0] = base_var
    for t in range(1, len(r)):
        h[t] = omega + alpha * (r[t - 1] ** 2) + beta * h[t - 1]
        if not np.isfinite(h[t]) or h[t] <= EPS:
            h[t] = base_var
    return np.maximum(h, EPS)


def fit_garch11(
    returns: np.ndarray,
    alpha_grid: tuple[float, ...] = (0.03, 0.05, 0.08, 0.10, 0.15),
    beta_grid: tuple[float, ...] = (0.75, 0.85, 0.90, 0.94, 0.97),
) -> UnivariateGarchFit:
    """Fits a univariate GARCH(1,1) by coarse Gaussian likelihood grid."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 12:
        raise ValueError("at least 12 returns required")
    r = r - np.mean(r)
    unconditional_var = max(float(np.var(r, ddof=1)), EPS)

    best: UnivariateGarchFit | None = None
    for alpha in alpha_grid:
        for beta in beta_grid:
            if alpha <= 0 or beta < 0 or alpha + beta >= 0.995:
                continue
            omega = max(unconditional_var * (1.0 - alpha - beta), EPS)
            h = _garch_variance_path(r, omega, alpha, beta)
            nll = 0.5 * float(np.sum(np.log(h) + (r ** 2) / h))
            if best is None or nll < best.neg_loglik:
                best = UnivariateGarchFit(omega, alpha, beta, h, nll)

    if best is None:
        alpha, beta = 0.05, 0.90
        omega = max(unconditional_var * (1.0 - alpha - beta), EPS)
        h = _garch_variance_path(r, omega, alpha, beta)
        best = UnivariateGarchFit(omega, alpha, beta, h, 0.5 * float(np.sum(np.log(h) + (r ** 2) / h)))
    return best


def _nearest_correlation(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=float)
    mat = (mat + mat.T) / 2.0
    vals, vecs = np.linalg.eigh(mat)
    vals = np.maximum(vals, 1e-6)
    psd = (vecs * vals) @ vecs.T
    d = np.sqrt(np.maximum(np.diag(psd), EPS))
    corr = psd / (d[:, None] * d[None, :])
    np.fill_diagonal(corr, 1.0)
    return np.clip(corr, -0.999, 0.999)


def _corr_from_q(q: np.ndarray) -> np.ndarray:
    d = np.sqrt(np.maximum(np.diag(q), EPS))
    r = q / (d[:, None] * d[None, :])
    np.fill_diagonal(r, 1.0)
    return np.clip(r, -0.999, 0.999)


def _dcc_likelihood(z: np.ndarray, qbar: np.ndarray, alpha: float, beta: float) -> tuple[float, np.ndarray]:
    t_obs, n_assets = z.shape
    q = qbar.copy()
    corrs = np.empty((t_obs, n_assets, n_assets), dtype=float)
    nll = 0.0

    for t in range(t_obs):
        if t > 0:
            outer = np.outer(z[t - 1], z[t - 1])
            q = (1.0 - alpha - beta) * qbar + alpha * outer + beta * q
            q = (q + q.T) / 2.0
        r = _corr_from_q(q)
        corrs[t] = r
        sign, logdet = np.linalg.slogdet(r)
        if sign <= 0 or not np.isfinite(logdet):
            r = _nearest_correlation(r)
            sign, logdet = np.linalg.slogdet(r)
        inv_r = np.linalg.pinv(r)
        nll += 0.5 * (logdet + float(z[t].T @ inv_r @ z[t]))
    return float(nll), corrs


def fit_dcc_garch(
    returns: pd.DataFrame | np.ndarray,
    dcc_alpha_grid: tuple[float, ...] = (0.01, 0.03, 0.05, 0.08, 0.12),
    dcc_beta_grid: tuple[float, ...] = (0.80, 0.88, 0.92, 0.95, 0.97),
) -> DCCGarchResult:
    """Fits DCC-GARCH and returns dynamic correlation matrices."""
    r, tickers = _as_array(returns)
    r_centered = r - np.nanmean(r, axis=0, keepdims=True)

    variance_cols: list[np.ndarray] = []
    standardized_cols: list[np.ndarray] = []
    for i in range(r_centered.shape[1]):
        fit = fit_garch11(r_centered[:, i])
        variance_cols.append(fit.variance)
        standardized_cols.append(r_centered[:, i] / np.sqrt(fit.variance))

    variances = np.column_stack(variance_cols)
    z = np.column_stack(standardized_cols)
    z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
    qbar = _nearest_correlation(np.corrcoef(z, rowvar=False))

    best: tuple[float, float, float, np.ndarray] | None = None
    for alpha in dcc_alpha_grid:
        for beta in dcc_beta_grid:
            if alpha <= 0 or beta < 0 or alpha + beta >= 0.995:
                continue
            nll, corrs = _dcc_likelihood(z, qbar, alpha, beta)
            if best is None or nll < best[2]:
                best = (alpha, beta, nll, corrs)

    if best is None:
        alpha, beta = 0.03, 0.95
        nll, corrs = _dcc_likelihood(z, qbar, alpha, beta)
    else:
        alpha, beta, nll, corrs = best

    return DCCGarchResult(
        tickers=tickers,
        alpha=float(alpha),
        beta=float(beta),
        unconditional_corr=qbar,
        correlations=corrs,
        volatilities=np.sqrt(variances),
        standardized_returns=z,
        neg_loglik=float(nll),
    )


def fit_dcc_from_prices(prices: pd.DataFrame) -> DCCGarchResult:
    """Convenience wrapper for price DataFrames."""
    returns = _to_returns_frame(prices, is_prices=True)
    if returns.empty:
        raise ValueError("price history does not contain enough usable returns")
    return fit_dcc_garch(returns)


def average_pairwise_correlation(corr: np.ndarray) -> float:
    corr = np.asarray(corr, dtype=float)
    if corr.ndim != 2 or corr.shape[0] < 2:
        return 0.0
    n = corr.shape[0]
    return float((corr.sum() - n) / (n * (n - 1)))


def dcc_regime_score(result: DCCGarchResult, baseline_window: int | None = None) -> float:
    """Scores how different latest DCC correlation is from its baseline."""
    corrs = result.correlations
    if len(corrs) < 3:
        return 0.0
    if baseline_window is None:
        baseline_window = max(3, len(corrs) // 2)
    baseline = np.mean(corrs[:baseline_window], axis=0)
    latest = corrs[-1]
    n = latest.shape[0]
    score = np.linalg.norm(latest - baseline, "fro") / (2.0 * n)
    return float(np.clip(score, 0.0, 1.0))


def result_summary(result: DCCGarchResult) -> dict:
    return {
        "n_assets": len(result.tickers),
        "n_obs": int(result.correlations.shape[0]),
        "dcc_alpha": result.alpha,
        "dcc_beta": result.beta,
        "avg_corr_latest": result.average_pairwise_latest,
        "regime_score": dcc_regime_score(result),
        "neg_loglik": result.neg_loglik,
    }
