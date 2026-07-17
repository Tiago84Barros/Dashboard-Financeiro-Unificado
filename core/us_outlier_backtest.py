"""
core/us_outlier_backtest.py
Backtest RETROSPECTIVO de "Empresas Fora da Curva" (multi-baggers).

Rótulos históricos configuráveis (ex.: 3× em 5 anos). O modelo (score de
assimetria conhecido em `as_of`) é avaliado contra o que a empresa possuía ANTES
da valorização — o rótulo usa o futuro apenas como alvo, nunca como feature. A
comparação com seleção aleatória usa a taxa-base (analítica, determinística).

Métricas do enunciado: acerto/precisão/recall, falsos positivos, distribuição de
retornos, contribuição das maiores vencedoras, e o resultado quando algumas
posições vão a zero. Puro; coberto por tests/test_us_outlier_backtest.py.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def _price_at(g: pd.DataFrame, when: pd.Timestamp, direction: str = "before") -> Optional[float]:
    if direction == "before":
        sub = g[g["month_end"] <= when]
        return float(sub.iloc[-1]["adjusted_close"]) if not sub.empty else None
    sub = g[g["month_end"] >= when]
    return float(sub.iloc[0]["adjusted_close"]) if not sub.empty else None


def multibagger_labels(monthly: pd.DataFrame, as_of, *, horizon_years: int = 5,
                       multiple: float = 3.0) -> dict:
    """symbol → True se o preço multiplicou por `multiple` dentro do horizonte.

    Usa o MÁXIMO no período (a tese acerta se em algum momento atingiu o alvo).
    """
    if monthly is None or monthly.empty:
        return {}
    m = monthly.dropna(subset=["adjusted_close"]).copy()
    m["month_end"] = pd.to_datetime(m["month_end"])
    as_of = pd.Timestamp(as_of)
    end = as_of + pd.DateOffset(years=horizon_years)
    out = {}
    for sym, g in m.groupby("symbol"):
        g = g.sort_values("month_end")
        p0 = _price_at(g, as_of, "before")
        if p0 is None or p0 <= 0:
            continue
        window = g[(g["month_end"] > as_of) & (g["month_end"] <= end)]
        if window.empty:
            continue
        out[sym] = bool(window["adjusted_close"].max() / p0 >= multiple)
    return out


def forward_total_return(monthly: pd.DataFrame, as_of, *, horizon_years: int = 5) -> dict:
    """symbol → retorno total (preço no fim do horizonte / preço em as_of − 1)."""
    if monthly is None or monthly.empty:
        return {}
    m = monthly.dropna(subset=["adjusted_close"]).copy()
    m["month_end"] = pd.to_datetime(m["month_end"])
    as_of = pd.Timestamp(as_of)
    end = as_of + pd.DateOffset(years=horizon_years)
    out = {}
    for sym, g in m.groupby("symbol"):
        g = g.sort_values("month_end")
        p0 = _price_at(g, as_of, "before")
        pend = _price_at(g[g["month_end"] <= end], end, "before")
        if p0 and pend and p0 > 0:
            out[sym] = pend / p0 - 1.0
    return out


def precision_recall(predicted: set, actual: set, universe: set) -> dict:
    """Precisão/recall/F1 + contagem de falsos positivos e lift sobre a taxa-base."""
    predicted = set(predicted) & set(universe)
    actual = set(actual) & set(universe)
    tp = len(predicted & actual)
    fp = len(predicted - actual)
    fn = len(actual - predicted)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)
    base_rate = len(actual) / len(universe) if universe else None
    lift = (precision / base_rate) if (precision and base_rate) else None
    return {"true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": precision, "recall": recall, "f1": f1,
            "base_rate": base_rate, "lift_vs_random": lift, "n_universe": len(universe)}


def return_distribution(returns) -> dict:
    """Estatísticas da distribuição de retornos (caudas importam nas assimétricas)."""
    s = pd.Series(list(returns), dtype="float64").dropna()
    if s.empty:
        return {"n": 0}
    return {"n": int(s.size), "mean": float(s.mean()), "median": float(s.median()),
            "p10": float(s.quantile(0.10)), "p90": float(s.quantile(0.90)),
            "max": float(s.max()), "min": float(s.min()),
            "pct_positive": float((s > 0).mean())}


def top_winner_contribution(returns, k: int = 1) -> Optional[float]:
    """Fração da riqueza terminal (equal-weight) vinda das k maiores vencedoras."""
    vals = [float(r) for r in returns if r is not None]
    if not vals:
        return None
    terminal = [1 + r for r in vals]
    total = sum(terminal)
    if total <= 0:
        return None
    top = sorted(terminal, reverse=True)[:max(1, k)]
    return sum(top) / total


def basket_return(returns, *, zero_fraction: float = 0.0) -> Optional[float]:
    """Retorno equal-weight da cesta; opcionalmente zera a pior fração (vão a zero)."""
    vals = sorted((float(r) for r in returns if r is not None))
    if not vals:
        return None
    n_zero = int(len(vals) * zero_fraction)
    vals = [-1.0] * n_zero + vals[n_zero:]      # os piores viram -100%
    terminal = sum(1 + r for r in vals) / len(vals)
    return terminal - 1.0
