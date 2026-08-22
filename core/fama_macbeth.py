"""
core/fama_macbeth.py - lightweight Fama-MacBeth style weight calibration.

The ideal academic version regresses next-period returns on current factors.
The App 4 database already has a rich fundamentals panel, while return history
is loaded later in the Streamlit flow. This module therefore implements a
conservative MVP: cross-sectional regressions by year using a forward
fundamental-quality proxy as the dependent variable. The estimated factor
importance is blended with the hand-tuned sector weights instead of replacing
them outright.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

DEFAULT_CANDIDATES: tuple[str, ...] = (
    "ROE",
    "ROIC",
    "Margem_Liquida",
    "Margem_Operacional",
    "DY",
    "P/L",
    "P/VP",
    "EV_EBIT",
    "Endividamento_Total",
    "Liquidez_Corrente",
)

FORWARD_PROXY_CONFIG: dict[str, tuple[float, bool]] = {
    "ROE": (0.22, True),
    "ROIC": (0.22, True),
    "Margem_Liquida": (0.16, True),
    "Margem_Operacional": (0.12, True),
    "DY": (0.08, True),
    "Endividamento_Total": (0.10, False),
    "P/L": (0.04, False),
    "P/VP": (0.03, False),
    "EV_EBIT": (0.03, False),
}

INVERSE_DEFAULTS = {"P/L", "P/VP", "EV_EBIT", "P_FCO", "Endividamento_Total"}


@dataclass(frozen=True)
class FamaMacbethResult:
    weights: dict[str, tuple[float, bool]]
    betas: dict[str, float]
    n_years: int
    n_tickers: int
    n_obs: int
    proxy_name: str
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.weights) and self.n_years > 0


def _empty_result(*warnings: str) -> FamaMacbethResult:
    return FamaMacbethResult(
        weights={},
        betas={},
        n_years=0,
        n_tickers=0,
        n_obs=0,
        proxy_name="forward_fundamental_proxy",
        warnings=tuple(warnings),
    )


def _annual_snapshots(
    hist_batch: dict[str, pd.DataFrame],
    columns: Iterable[str],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    cols = list(dict.fromkeys(columns))
    for ticker, df in (hist_batch or {}).items():
        if df is None or df.empty or "Data" not in df.columns:
            continue
        tmp = df.copy()
        tmp["Data"] = pd.to_datetime(tmp["Data"], errors="coerce")
        tmp = tmp.dropna(subset=["Data"]).sort_values("Data")
        if tmp.empty:
            continue
        tmp["_year"] = tmp["Data"].dt.year
        for year, group in tmp.groupby("_year", sort=True):
            last = group.iloc[-1]
            row: dict[str, object] = {
                "Ticker": str(ticker).upper().replace(".SA", ""),
                "year": int(year),
            }
            for col in cols:
                if col in tmp.columns:
                    row[col] = pd.to_numeric(pd.Series([last[col]]), errors="coerce").iloc[0]
            rows.append(row)
    return pd.DataFrame(rows)


def _rank_by_year(panel: pd.DataFrame, col: str, higher_is_better: bool) -> pd.Series:
    vals = pd.to_numeric(panel[col], errors="coerce")
    return vals.groupby(panel["year"]).rank(pct=True, ascending=higher_is_better)


def _forward_proxy(panel: pd.DataFrame) -> pd.Series:
    parts: list[pd.Series] = []
    weights: list[float] = []
    for col, (weight, higher) in FORWARD_PROXY_CONFIG.items():
        if col not in panel.columns:
            continue
        ranked = _rank_by_year(panel, col, higher_is_better=higher)
        if ranked.notna().sum() == 0:
            continue
        parts.append(ranked * float(weight))
        weights.append(float(weight))
    if not parts or not weights:
        return pd.Series(np.nan, index=panel.index, dtype=float)
    total = sum(weights) or 1.0
    return pd.concat(parts, axis=1).sum(axis=1, min_count=1) / total


def _standardize_by_year(panel: pd.DataFrame, col: str) -> pd.Series:
    vals = pd.to_numeric(panel[col], errors="coerce")

    def _zscore(s: pd.Series) -> pd.Series:
        s = s.copy()
        valid = s.dropna()
        if len(valid) < 3:
            return pd.Series(np.nan, index=s.index, dtype=float)
        lo, hi = valid.quantile([0.05, 0.95])
        s = s.clip(lower=lo, upper=hi)
        std = s.std(ddof=0)
        if not np.isfinite(std) or std < 1e-12:
            return pd.Series(0.0, index=s.index, dtype=float)
        return (s - s.mean()) / std

    return vals.groupby(panel["year"], group_keys=False).apply(_zscore)


def _ols_betas(x: pd.DataFrame, y: pd.Series) -> np.ndarray | None:
    data = pd.concat([y.rename("_target"), x], axis=1).dropna(subset=["_target"])
    if data.empty:
        return None
    feature_cols = list(x.columns)
    data[feature_cols] = data[feature_cols].fillna(0.0)
    if len(data) <= max(3, min(len(feature_cols), 8)):
        return None
    yv = data["_target"].to_numpy(dtype=float)
    xv = data[feature_cols].to_numpy(dtype=float)
    xv = np.column_stack([np.ones(len(xv)), xv])
    try:
        beta = np.linalg.lstsq(xv, yv, rcond=None)[0][1:]
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(beta)):
        return None
    return beta


def estimate_fama_macbeth_weights(
    hist_batch: dict[str, pd.DataFrame],
    candidate_indicators: Iterable[str] | None = None,
    min_years: int = 3,
    min_obs_per_year: int = 8,
) -> FamaMacbethResult:
    """Estimate indicator weights from cross-sectional yearly regressions."""
    candidates = [
        c for c in (candidate_indicators or DEFAULT_CANDIDATES)
        if c in DEFAULT_CANDIDATES
    ]
    if not candidates:
        return _empty_result("sem_indicadores_candidatos")

    needed = set(candidates) | set(FORWARD_PROXY_CONFIG)
    panel = _annual_snapshots(hist_batch, needed)
    if panel.empty:
        return _empty_result("sem_painel_historico")

    panel = panel.sort_values(["Ticker", "year"]).reset_index(drop=True)
    panel["_proxy_now"] = _forward_proxy(panel)
    panel["_target"] = panel.groupby("Ticker")["_proxy_now"].shift(-1)
    panel = panel.dropna(subset=["_target"])
    if panel.empty:
        return _empty_result("sem_alvo_forward")

    feature_cols = [c for c in candidates if c in panel.columns]
    if not feature_cols:
        return _empty_result("sem_features_validas")

    x_all = pd.DataFrame({
        col: _standardize_by_year(panel, col)
        for col in feature_cols
    }, index=panel.index)

    yearly_betas: list[np.ndarray] = []
    yearly_obs = 0
    used_years = 0
    for year, idx in panel.groupby("year").groups.items():
        x = x_all.loc[idx]
        y = panel.loc[idx, "_target"]
        if y.notna().sum() < min_obs_per_year:
            continue
        beta = _ols_betas(x, y)
        if beta is None:
            continue
        yearly_betas.append(beta)
        yearly_obs += int(y.notna().sum())
        used_years += 1

    if used_years < min_years or not yearly_betas:
        return _empty_result("historico_insuficiente")

    beta_mean = np.vstack(yearly_betas).mean(axis=0)
    abs_beta = np.abs(beta_mean)
    total = float(abs_beta.sum())
    if not np.isfinite(total) or total <= 1e-12:
        return _empty_result("betas_sem_sinal")

    weights: dict[str, tuple[float, bool]] = {}
    betas: dict[str, float] = {}
    for col, beta, mag in zip(feature_cols, beta_mean, abs_beta):
        if not np.isfinite(beta) or mag <= 1e-12:
            continue
        betas[col] = float(beta)
        higher_is_better = bool(beta >= 0)
        weights[col] = (float(mag / total), higher_is_better)

    if not weights:
        return _empty_result("sem_pesos_estimados")

    return FamaMacbethResult(
        weights=weights,
        betas=betas,
        n_years=used_years,
        n_tickers=int(panel["Ticker"].nunique()),
        n_obs=yearly_obs,
        proxy_name="forward_fundamental_proxy",
    )


def normalize_weights(conf: dict[str, tuple[float, bool]]) -> dict[str, tuple[float, bool]]:
    total = sum(max(float(v[0]), 0.0) for v in conf.values()) or 1.0
    return {
        key: (max(float(weight), 0.0) / total, bool(higher))
        for key, (weight, higher) in conf.items()
        if float(weight) > 0
    }


def blend_with_base_weights(
    base_weights: dict[str, tuple[float, bool]],
    fm_result: FamaMacbethResult,
    alpha: float = 0.35,
) -> dict[str, tuple[float, bool]]:
    """Blend hand-tuned weights with Fama-MacBeth MVP weights."""
    base = normalize_weights(base_weights)
    if not fm_result.ok:
        return base

    alpha = float(np.clip(alpha, 0.0, 0.85))
    fm = normalize_weights(fm_result.weights)
    keys = sorted(set(base) | set(fm))
    blended: dict[str, tuple[float, bool]] = {}
    for key in keys:
        base_weight, base_high = base.get(
            key,
            (0.0, key not in INVERSE_DEFAULTS and "endiv" not in key.lower()),
        )
        fm_weight, fm_high = fm.get(key, (0.0, base_high))
        weight = (1.0 - alpha) * base_weight + alpha * fm_weight
        if weight <= 0:
            continue
        direction = fm_high if key in fm_result.betas else base_high
        blended[key] = (weight, direction)
    return normalize_weights(blended)


# ──────────────────────────────────────────────────────────────────────────
# A5+ (2026-05-27): Rolling 5y window calibration
# ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FamaMacbethRollingResult:
    """Resultado da estimação rolling de Fama-MacBeth.

    Cada janela de `window_years` anos consecutivos gera um conjunto
    de pesos. Agregamos via mediana (robusta a janelas anômalas tipo
    2020 COVID) e reportamos dispersão (IQR e std) por indicador.
    """
    weights:          dict[str, tuple[float, bool]]  # mediana entre janelas
    weights_per_window: list[dict[str, tuple[float, bool]]]
    betas_per_window:   list[dict[str, float]]
    window_years:     int
    n_windows:        int
    year_ranges:      list[tuple[int, int]]
    weight_dispersion: dict[str, dict[str, float]]   # {indicador: {std, iqr}}
    warnings:         tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.weights) and self.n_windows > 0

    def as_static_result(self) -> FamaMacbethResult:
        """Compatibilidade: devolve FamaMacbethResult para uso em
        blend_with_base_weights (caller não precisa saber que é rolling)."""
        # Beta médio entre janelas para preservar sinal
        if not self.betas_per_window:
            return _empty_result("rolling_sem_betas")
        all_betas: dict[str, list[float]] = {}
        for bp in self.betas_per_window:
            for k, v in bp.items():
                all_betas.setdefault(k, []).append(v)
        beta_agg = {k: float(np.median(v)) for k, v in all_betas.items()}
        return FamaMacbethResult(
            weights=self.weights, betas=beta_agg,
            n_years=self.window_years * self.n_windows,
            n_tickers=0, n_obs=0,
            proxy_name=f"forward_fundamental_proxy_rolling_{self.window_years}y",
            warnings=self.warnings,
        )


def _aggregate_window_weights(
    weights_list: list[dict[str, tuple[float, bool]]],
) -> tuple[dict[str, tuple[float, bool]], dict[str, dict[str, float]]]:
    """Mediana dos pesos entre janelas + dispersão (std, IQR) por indicador."""
    if not weights_list:
        return {}, {}
    # Coleta peso de cada indicador em todas as janelas (0 quando ausente)
    keys = set()
    for w in weights_list:
        keys.update(w.keys())
    by_indicator: dict[str, list[float]] = {k: [] for k in keys}
    direction_votes: dict[str, list[bool]] = {k: [] for k in keys}
    for w in weights_list:
        for k in keys:
            tpl = w.get(k, (0.0, True))
            by_indicator[k].append(float(tpl[0]))
            direction_votes[k].append(bool(tpl[1]))
    # Mediana de peso + direção majoritária
    median_w: dict[str, tuple[float, bool]] = {}
    dispersion: dict[str, dict[str, float]] = {}
    for k, vals in by_indicator.items():
        arr = np.array(vals, dtype=float)
        med = float(np.median(arr))
        if med <= 1e-9:
            continue
        # Direção majoritária entre janelas com peso > 0
        positive_dirs = [d for d, v in zip(direction_votes[k], vals) if v > 0]
        higher = bool(sum(positive_dirs) >= len(positive_dirs) / 2.0)
        median_w[k] = (med, higher)
        q25, q75 = np.percentile(arr, [25, 75])
        dispersion[k] = {
            "median": med,
            "std":    float(np.std(arr, ddof=0)),
            "iqr":    float(q75 - q25),
            "min":    float(arr.min()),
            "max":    float(arr.max()),
        }
    # Renormaliza para somar 1
    total = sum(v[0] for v in median_w.values()) or 1.0
    median_w = {k: (v[0] / total, v[1]) for k, v in median_w.items()}
    return median_w, dispersion


def estimate_fama_macbeth_rolling(
    hist_batch: dict[str, pd.DataFrame],
    candidate_indicators: Iterable[str] | None = None,
    window_years: int = 5,
    step_years: int = 1,
    min_obs_per_year: int = 8,
) -> FamaMacbethRollingResult:
    """Calibra pesos via janelas rolantes de `window_years` anos.

    Em vez de uma única estimativa (que sobre-ajusta ao período disponível),
    desliza janelas de 5y com step=1y e agrega via mediana. Reduz overfit
    a períodos anômalos (2008, 2015, 2020) e expõe estabilidade temporal
    dos coeficientes via dispersão (IQR/std).

    Args:
      hist_batch:    {ticker: DataFrame com Data + indicadores}
      candidate_indicators: subconjunto de DEFAULT_CANDIDATES (ou None = todos)
      window_years:  largura da janela (default 5y, padrão academia)
      step_years:    passo entre janelas (default 1y → janelas sobrepostas)
      min_obs_per_year: obs mínimas no cross-section anual

    Returns:
      FamaMacbethRollingResult com mediana, dispersão e ranges de cada janela.

    Para uso no scoring, chamar `.as_static_result()` para obter um
    FamaMacbethResult compatível com `blend_with_base_weights`.
    """
    candidates = [
        c for c in (candidate_indicators or DEFAULT_CANDIDATES)
        if c in DEFAULT_CANDIDATES
    ]
    if not candidates:
        return FamaMacbethRollingResult(
            weights={}, weights_per_window=[], betas_per_window=[],
            window_years=window_years, n_windows=0, year_ranges=[],
            weight_dispersion={}, warnings=("sem_indicadores_candidatos",),
        )

    needed = set(candidates) | set(FORWARD_PROXY_CONFIG)
    panel_full = _annual_snapshots(hist_batch, needed)
    if panel_full.empty:
        return FamaMacbethRollingResult(
            weights={}, weights_per_window=[], betas_per_window=[],
            window_years=window_years, n_windows=0, year_ranges=[],
            weight_dispersion={}, warnings=("sem_painel_historico",),
        )

    panel_full = panel_full.sort_values(["Ticker", "year"]).reset_index(drop=True)
    years_avail = sorted(panel_full["year"].unique())
    if len(years_avail) < window_years + 1:
        # Não tem histórico suficiente para nem 1 janela completa
        return FamaMacbethRollingResult(
            weights={}, weights_per_window=[], betas_per_window=[],
            window_years=window_years, n_windows=0, year_ranges=[],
            weight_dispersion={},
            warnings=(f"hist_insuficiente_{len(years_avail)}y",),
        )

    windows: list[tuple[int, int]] = []
    for i in range(0, len(years_avail) - window_years + 1, step_years):
        yr_start = years_avail[i]
        yr_end = years_avail[i + window_years - 1]
        windows.append((yr_start, yr_end))

    weights_per_window: list[dict[str, tuple[float, bool]]] = []
    betas_per_window: list[dict[str, float]] = []
    successful_ranges: list[tuple[int, int]] = []

    for (yr_start, yr_end) in windows:
        # Filtra hist_batch para a janela
        win_hist = {}
        for tk, df in hist_batch.items():
            if df is None or df.empty or "Data" not in df.columns:
                continue
            tmp = df.copy()
            tmp["Data"] = pd.to_datetime(tmp["Data"], errors="coerce")
            tmp = tmp.dropna(subset=["Data"])
            mask = (tmp["Data"].dt.year >= yr_start) & (tmp["Data"].dt.year <= yr_end)
            sub = tmp[mask]
            if not sub.empty:
                win_hist[tk] = sub

        res = estimate_fama_macbeth_weights(
            win_hist, candidate_indicators=candidates,
            min_years=max(1, window_years - 2),
            min_obs_per_year=min_obs_per_year,
        )
        if res.ok:
            weights_per_window.append(res.weights)
            betas_per_window.append(res.betas)
            successful_ranges.append((yr_start, yr_end))

    if not weights_per_window:
        return FamaMacbethRollingResult(
            weights={}, weights_per_window=[], betas_per_window=[],
            window_years=window_years, n_windows=0, year_ranges=[],
            weight_dispersion={}, warnings=("nenhuma_janela_convergiu",),
        )

    median_weights, dispersion = _aggregate_window_weights(weights_per_window)

    return FamaMacbethRollingResult(
        weights=median_weights,
        weights_per_window=weights_per_window,
        betas_per_window=betas_per_window,
        window_years=window_years,
        n_windows=len(weights_per_window),
        year_ranges=successful_ranges,
        weight_dispersion=dispersion,
    )
