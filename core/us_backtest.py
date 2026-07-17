"""
core/us_backtest.py
Backtest walk-forward POINT-IN-TIME e validação por Rank-IC (puro, testável).

Entrada canônica: um painel LONGO com colunas ['date','symbol','score','fwd_return'],
onde `score` é conhecido em `date` (available_at ≤ date) e `fwd_return` é o retorno
realizado no período SEGUINTE — o alinhamento PIT é responsabilidade de quem monta
o painel (core.us_read.load_score_panel / data_pipeline.us.scoring_history).

Nada aqui usa dado futuro além do fwd_return já alinhado. Métricas: Rank-IC médio,
t-stat, p-valor, hit rate, excesso sobre equal-weight, Sharpe/Sortino/Calmar,
drawdown, turnover. Coberto por tests/test_us_backtest.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rank_ic(scores: pd.Series, fwd: pd.Series) -> float | None:
    """Spearman rank correlation entre score e retorno futuro (um período)."""
    df = pd.DataFrame({"s": scores, "f": fwd}).dropna()
    if len(df) < 3 or df["s"].nunique() < 2 or df["f"].nunique() < 2:
        return None
    return float(df["s"].rank().corr(df["f"].rank()))


def rank_ic_series(panel: pd.DataFrame) -> pd.Series:
    """Rank-IC por data sobre todo o cross-section daquela data."""
    out = {}
    for date, g in panel.groupby("date"):
        ic = rank_ic(g["score"], g["fwd_return"])
        if ic is not None:
            out[date] = ic
    return pd.Series(out, dtype="float64").sort_index()


def ic_summary(ic: pd.Series) -> dict:
    """Média, t-stat, p-valor (t bicaudal) e hit rate do Rank-IC."""
    ic = ic.dropna()
    n = len(ic)
    if n == 0:
        return {"n": 0, "mean": None, "std": None, "t_stat": None,
                "p_value": None, "hit_rate": None}
    mean = float(ic.mean())
    std = float(ic.std(ddof=1)) if n > 1 else 0.0
    t_stat = mean / (std / np.sqrt(n)) if std > 0 else None
    p_value = None
    if t_stat is not None and n > 1:
        try:
            from scipy import stats
            p_value = float(2 * stats.t.sf(abs(t_stat), df=n - 1))
        except Exception:  # scipy ausente: deixa None
            p_value = None
    return {"n": n, "mean": mean, "std": std,
            "t_stat": None if t_stat is None else float(t_stat),
            "p_value": p_value, "hit_rate": float((ic > 0).mean())}


def performance_stats(returns: pd.Series, periods_per_year: int = 12,
                      rf: float = 0.0) -> dict:
    """Retorno/vol/Sharpe/Sortino/Calmar/drawdown de uma série de retornos."""
    r = pd.Series(returns).dropna().astype(float)
    n = len(r)
    if n == 0:
        return {k: None for k in ("total_return", "ann_return", "volatility",
                                  "sharpe", "sortino", "calmar", "max_drawdown", "n")} | {"n": 0}
    curve = (1 + r).cumprod()
    total = float(curve.iloc[-1] - 1)
    ann = float(curve.iloc[-1] ** (periods_per_year / n) - 1)
    vol = float(r.std(ddof=1) * np.sqrt(periods_per_year)) if n > 1 else 0.0
    rf_p = rf / periods_per_year
    excess = r - rf_p
    sharpe = float(excess.mean() / r.std(ddof=1) * np.sqrt(periods_per_year)) \
        if n > 1 and r.std(ddof=1) > 0 else None
    downside = r[r < 0]
    dstd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = float(excess.mean() / dstd * np.sqrt(periods_per_year)) if dstd > 0 else None
    peak = curve.cummax()
    mdd = float(((curve - peak) / peak).min())
    calmar = float(ann / abs(mdd)) if mdd < 0 else None
    return {"total_return": total, "ann_return": ann, "volatility": vol,
            "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
            "max_drawdown": mdd, "n": n}


def turnover(prev: dict, new: dict) -> float:
    """Turnover entre duas carteiras (0.5·Σ|Δw|). 1.0 = troca total."""
    symbols = set(prev) | set(new)
    return 0.5 * sum(abs(new.get(s, 0.0) - prev.get(s, 0.0)) for s in symbols)


def _period_weights(g: pd.DataFrame, top_n: int, weighting: str) -> dict:
    sel = g.sort_values("score", ascending=False).head(top_n)
    if sel.empty:
        return {}
    if weighting == "score":
        base = (sel["score"] - sel["score"].min()) + 1e-6
        w = base / base.sum()
    else:  # equal
        w = pd.Series(1.0 / len(sel), index=sel.index)
    return dict(zip(sel["symbol"], w))


def walk_forward(panel: pd.DataFrame, *, top_n: int = 20,
                 weighting: str = "score", periods_per_year: int = 12) -> dict:
    """Backtest PIT: em cada data forma a carteira pelos scores e realiza o
    fwd_return; compara com equal-weight do universo. Retorna curvas + métricas.
    """
    if panel is None or panel.empty:
        return {"ok": False, "reason": "painel vazio"}
    panel = panel.dropna(subset=["score", "fwd_return"])
    port_ret, ew_ret, dates = [], [], []
    prev_w: dict = {}
    turns = []
    for date, g in panel.groupby("date"):
        w = _period_weights(g, top_n, weighting)
        if not w:
            continue
        fwd = dict(zip(g["symbol"], g["fwd_return"]))
        pr = sum(wi * fwd.get(s, 0.0) for s, wi in w.items())
        port_ret.append(pr)
        ew_ret.append(float(g["fwd_return"].mean()))
        dates.append(date)
        turns.append(turnover(prev_w, w))
        prev_w = w
    if not dates:
        return {"ok": False, "reason": "sem períodos utilizáveis"}
    pr_s = pd.Series(port_ret, index=dates)
    ew_s = pd.Series(ew_ret, index=dates)
    ic = rank_ic_series(panel)
    ic_stats = ic_summary(ic)
    p_stats = performance_stats(pr_s, periods_per_year)
    ew_stats = performance_stats(ew_s, periods_per_year)
    excess = (p_stats["ann_return"] - ew_stats["ann_return"]) \
        if p_stats["ann_return"] is not None and ew_stats["ann_return"] is not None else None
    return {
        "ok": True, "n_periods": len(dates),
        "start_date": min(dates), "end_date": max(dates),
        "rank_ic": ic_stats,
        "portfolio": p_stats, "equal_weight": ew_stats,
        "excess_ann_vs_ew": excess,
        "avg_turnover": float(np.mean(turns)) if turns else None,
        "equity_curve": list((1 + pr_s).cumprod().values),
        "dates": [str(d) for d in dates],
    }
