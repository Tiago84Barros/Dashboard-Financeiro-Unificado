"""Testes do motor de backtest / Rank-IC (PIT)."""
import numpy as np
import pandas as pd
import pytest

import core.us_backtest as bt


def test_rank_ic_perfeito():
    scores = pd.Series([1, 2, 3, 4, 5])
    fwd = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])   # monotônico → IC = 1
    assert bt.rank_ic(scores, fwd) == pytest.approx(1.0)
    assert bt.rank_ic(scores, fwd[::-1].reset_index(drop=True)) == pytest.approx(-1.0)


def test_rank_ic_degenerado():
    assert bt.rank_ic(pd.Series([1, 1, 1]), pd.Series([1, 2, 3])) is None
    assert bt.rank_ic(pd.Series([1, 2]), pd.Series([1, 2])) is None   # n<3


def _panel(periods=8, names=6, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(periods):
        for i in range(names):
            score = names - i           # S0 melhor
            # retorno correlacionado ao score + ruído
            fwd = 0.01 * (names - i) + rng.normal(0, 0.01)
            rows.append({"date": f"2020-{d+1:02d}", "symbol": f"S{i}",
                         "score": score, "fwd_return": fwd})
    return pd.DataFrame(rows)


def test_rank_ic_series_e_summary():
    ic = bt.rank_ic_series(_panel())
    assert len(ic) == 8
    s = bt.ic_summary(ic)
    assert s["n"] == 8 and s["mean"] > 0.5 and s["hit_rate"] == 1.0
    assert s["t_stat"] is not None


def test_performance_stats():
    r = pd.Series([0.10, -0.05, 0.08, 0.02, 0.03])
    st = bt.performance_stats(r, periods_per_year=12)
    assert st["n"] == 5
    assert st["total_return"] == pytest.approx((1.1*0.95*1.08*1.02*1.03) - 1)
    assert st["max_drawdown"] <= 0
    assert st["volatility"] > 0


def test_turnover():
    assert bt.turnover({"A": 0.5, "B": 0.5}, {"A": 0.5, "B": 0.5}) == pytest.approx(0.0)
    assert bt.turnover({"A": 1.0}, {"B": 1.0}) == pytest.approx(1.0)


def test_walk_forward():
    res = bt.walk_forward(_panel(), top_n=2, weighting="equal")
    assert res["ok"] and res["n_periods"] == 8
    # carteira dos melhores scores deve superar equal-weight (fwd cresce com score)
    assert res["portfolio"]["ann_return"] > res["equal_weight"]["ann_return"]
    assert res["excess_ann_vs_ew"] > 0
    assert res["rank_ic"]["mean"] > 0
    assert len(res["equity_curve"]) == 8


def test_walk_forward_vazio():
    assert bt.walk_forward(pd.DataFrame())["ok"] is False


def test_periods_per_year_afeta_anualizacao():
    """Painel anual com periods_per_year=1 não deve compor 12× (bug real na 1a carga)."""
    # 3 períodos anuais, retorno constante 10% no topo
    rows = []
    for d in range(3):
        for i in range(5):
            rows.append({"date": f"20{20+d}", "symbol": f"S{i}",
                         "score": 5 - i, "fwd_return": 0.10 if i == 0 else 0.0})
    panel = pd.DataFrame(rows)
    anual = bt.walk_forward(panel, top_n=1, weighting="equal", periods_per_year=1)
    # top-1 rende 10% ao ano, 3 anos → ann_return ~10%, não centenas de %
    assert anual["portfolio"]["ann_return"] == pytest.approx(0.10, abs=1e-6)
    mensal = bt.walk_forward(panel, top_n=1, weighting="equal", periods_per_year=12)
    assert mensal["portfolio"]["ann_return"] > anual["portfolio"]["ann_return"]  # compõe demais
