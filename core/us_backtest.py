"""
core/us_backtest.py
Backtest walk-forward POINT-IN-TIME e validação por Rank-IC (puro, testável).

Entrada canônica: um painel LONGO com colunas ['date','symbol','score','fwd_return'],
onde `score` é conhecido em `date` (available_at ≤ date) e `fwd_return` é o retorno
realizado no período SEGUINTE — o alinhamento PIT é responsabilidade de quem monta
o painel (core.us_read.load_score_panel / data_pipeline.us.scoring_history).

Nada aqui usa dado futuro além do fwd_return já alinhado. Métricas: Rank-IC médio,
t-stat, p-valor, hit rate, excesso sobre equal-weight, excesso sobre o índice de
mercado escolhido (core.us_benchmark), Sharpe/Sortino/Calmar, drawdown, turnover.
Coberto por tests/test_us_backtest.py e tests/test_us_backtest_benchmark.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# O backtest preserva o score-weighting histórico sem aplicar water-filling; ele
# é, portanto, uma estratégia TEÓRICA. A política de referência da carteira EUA
# limita uma posição a 10% (core.us_portfolio.PortfolioConstraints). Resultado
# acima disso continua auditável, mas não é elegível para conclusão de estratégia.
BACKTEST_POLICY_MAX_WEIGHT = 0.10


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
    r = pd.to_numeric(pd.Series(returns), errors="coerce").dropna().astype(float)
    if not r.empty and not np.isfinite(r.to_numpy()).all():
        return {k: None for k in ("total_return", "ann_return", "volatility",
                                  "sharpe", "sortino", "calmar", "max_drawdown", "n")} | {"n": 0}
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


def bootstrap_mean_ci(values, *, samples: int = 2000, confidence: float = 0.95,
                      seed: int = 42) -> dict:
    """Intervalo bootstrap reprodutível da média dos retornos/excessos."""
    x = np.asarray(pd.Series(values).dropna(), dtype=float)
    if len(x) < 2 or samples <= 0:
        return {"n": int(len(x)), "samples": int(samples), "mean": None,
                "ci_low": None, "ci_high": None}
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(samples, len(x)), replace=True).mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    return {"n": int(len(x)), "samples": int(samples), "mean": float(x.mean()),
            "ci_low": float(np.quantile(means, alpha)),
            "ci_high": float(np.quantile(means, 1.0 - alpha))}


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


def _bloco_benchmark(pr_s: pd.Series, dates: list, *, benchmark,
                     benchmark_series, benchmark_loader,
                     horizonte_meses: int | None, periods_per_year: int,
                     min_obs: int, bootstrap_samples: int) -> dict:
    """Métricas contra o índice escolhido, ou o estado de erro nomeado.

    Duas decisões de que o resultado depende e que não são óbvias:

    1. o excesso é medido SÓ nas datas em que o benchmark tem série. Anualizar a
       carteira sobre 8 janelas e o índice sobre 5 e subtrair daria um "excesso"
       que é diferença de calendário, não de habilidade — por isso a carteira é
       REMEDIDA na janela do benchmark (``portfolio_on_benchmark_window``);
    2. em qualquer erro, NENHUMA chave de excesso é devolvida. Chave ausente é o
       que garante que a UI não exiba excesso contra um benchmark que não foi
       lido — devolver ``None`` convidaria a formatar "0,00%".
    """
    import core.us_benchmark as _bm

    horizonte = (_bm.horizonte_padrao(periods_per_year)
                 if horizonte_meses is None else int(horizonte_meses))
    estado = _bm.preparar(benchmark, dates, horizonte_meses=horizonte,
                          loader=benchmark_loader, serie=benchmark_series,
                          min_obs=min_obs)
    if not estado.get("ok") or estado.get("modo") != _bm.MODO_INDICE:
        return {"benchmark": {k: v for k, v in estado.items() if k != "retornos"}}
    bm_s = estado.pop("retornos")
    comum = [d for d in dates if d in set(bm_s.index)]
    port_janela = pr_s.loc[comum]
    bm_janela = bm_s.loc[comum]
    bm_stats = performance_stats(bm_janela, periods_per_year)
    port_stats = performance_stats(port_janela, periods_per_year)
    excesso = (port_stats["ann_return"] - bm_stats["ann_return"]
               if port_stats["ann_return"] is not None
               and bm_stats["ann_return"] is not None else None)
    return {
        "benchmark": estado,
        "benchmark_stats": bm_stats,
        "portfolio_on_benchmark_window": port_stats,
        "excess_ann_vs_benchmark": excesso,
        "bootstrap_excess_vs_benchmark": bootstrap_mean_ci(
            port_janela - bm_janela, samples=bootstrap_samples),
        "benchmark_equity_curve": list((1 + bm_janela).cumprod().values),
        "benchmark_dates": [str(d) for d in comum],
    }


def walk_forward(panel: pd.DataFrame, *, top_n: int = 20,
                 weighting: str = "score", periods_per_year: int = 12,
                 transaction_cost_bps: float = 0.0, slippage_bps: float = 0.0,
                 bootstrap_samples: int = 2000,
                 benchmark=None, benchmark_series: pd.Series | None = None,
                 benchmark_loader=None,
                 benchmark_horizon_months: int | None = None,
                 benchmark_min_obs: int | None = None) -> dict:
    """Backtest PIT: em cada data forma a carteira pelos scores e realiza o
    fwd_return; compara com equal-weight do universo. Retorna curvas + métricas.

    ``benchmark`` (rótulo do seletor da UI, símbolo ou ``SelecaoBenchmark``)
    ACRESCENTA um índice de mercado à comparação — ele não substitui o
    equal-weight. As duas baselines respondem perguntas diferentes: o
    equal-weight mede se o score seleciona melhor DENTRO do universo elegível; o
    índice mede se a estratégia inteira valeu a pena contra comprar o mercado.
    Por isso ``excess_ann_vs_ew`` e ``excess_ann_vs_benchmark`` coexistem e têm
    nomes distintos.

    Sem ``benchmark``, o retorno é exatamente o de antes (nenhuma chave nova).
    Com benchmark inválido, sem série ou com interseção temporal abaixo do piso,
    ``res["benchmark"]["ok"]`` é ``False`` com ``erro``/``mensagem`` nomeados e
    NENHUMA métrica de excesso contra índice é devolvida.
    """
    if panel is None or panel.empty:
        return {"ok": False, "reason": "painel vazio"}
    panel = panel.copy()
    for coluna in ("score", "fwd_return"):
        panel[coluna] = pd.to_numeric(panel[coluna], errors="coerce")
    panel = panel.dropna(subset=["score", "fwd_return"])
    if panel.empty:
        return {"ok": False, "reason": "sem períodos utilizáveis"}
    if not np.isfinite(panel[["score", "fwd_return"]].to_numpy(dtype=float)).all():
        # Um +/-inf não é uma observação extrema: é dado inválido. Não o
        # removemos silenciosamente porque a seleção e o retorno do período
        # poderiam mudar sem que quem lê o backtest soubesse.
        return {"ok": False, "reason": "retornos ou scores não finitos"}
    port_ret, gross_ret, ew_ret, dates = [], [], [], []
    prev_w: dict = {}
    turns = []
    max_weights, hhis = [], []
    for date, g in panel.groupby("date"):
        w = _period_weights(g, top_n, weighting)
        if not w:
            continue
        fwd = dict(zip(g["symbol"], g["fwd_return"]))
        pr = sum(wi * fwd.get(s, 0.0) for s, wi in w.items())
        turn = turnover(prev_w, w)
        cost = turn * (float(transaction_cost_bps) + float(slippage_bps)) / 10_000.0
        gross_ret.append(pr)
        port_ret.append(pr - cost)
        ew_ret.append(float(g["fwd_return"].mean()))
        dates.append(date)
        turns.append(turn)
        pesos = np.fromiter(w.values(), dtype=float)
        max_weights.append(float(pesos.max()))
        hhis.append(float(np.square(pesos).sum()))
        prev_w = w
    if not dates:
        return {"ok": False, "reason": "sem períodos utilizáveis"}
    pr_s = pd.Series(port_ret, index=dates)
    gross_s = pd.Series(gross_ret, index=dates)
    ew_s = pd.Series(ew_ret, index=dates)
    ic = rank_ic_series(panel)
    ic_stats = ic_summary(ic)
    p_stats = performance_stats(pr_s, periods_per_year)
    gross_stats = performance_stats(gross_s, periods_per_year)
    ew_stats = performance_stats(ew_s, periods_per_year)
    excess = (p_stats["ann_return"] - ew_stats["ann_return"]) \
        if p_stats["ann_return"] is not None and ew_stats["ann_return"] is not None else None
    excess_series = pr_s - ew_s
    extra: dict = {}
    if benchmark is not None or benchmark_series is not None:
        import core.us_benchmark as _bm
        extra = _bloco_benchmark(
            pr_s, dates, benchmark=benchmark, benchmark_series=benchmark_series,
            benchmark_loader=benchmark_loader,
            horizonte_meses=benchmark_horizon_months,
            periods_per_year=periods_per_year,
            min_obs=(_bm.MIN_OBS_BENCHMARK if benchmark_min_obs is None
                     else int(benchmark_min_obs)),
            bootstrap_samples=bootstrap_samples)
    max_weight = float(max(max_weights)) if max_weights else None
    max_hhi = float(max(hhis)) if hhis else None
    violates_policy = bool(max_weight is not None
                           and max_weight > BACKTEST_POLICY_MAX_WEIGHT + 1e-12)
    return {
        "ok": True, "n_periods": len(dates),
        "start_date": min(dates), "end_date": max(dates),
        "rank_ic": ic_stats,
        "portfolio": p_stats, "equal_weight": ew_stats,
        "portfolio_gross": gross_stats,
        "excess_ann_vs_ew": excess,
        "avg_turnover": float(np.mean(turns)) if turns else None,
        "concentration": {
            "strategy_mode": "teorica_sem_cap",
            "policy_max_weight": BACKTEST_POLICY_MAX_WEIGHT,
            "max_weight": max_weight,
            "max_hhi": max_hhi,
            "violates_policy": violates_policy,
            "eligible_for_conclusion": not violates_policy,
        },
        "transaction_cost_bps": float(transaction_cost_bps),
        "slippage_bps": float(slippage_bps),
        "bootstrap_excess": bootstrap_mean_ci(excess_series, samples=bootstrap_samples),
        "equity_curve": list((1 + pr_s).cumprod().values),
        "dates": [str(d) for d in dates],
        **extra,
    }
