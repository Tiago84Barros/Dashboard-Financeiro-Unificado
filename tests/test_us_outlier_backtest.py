"""Testes do backtest retrospectivo de multi-baggers."""
import pandas as pd
import pytest

import core.us_outlier_backtest as ob


def _monthly():
    # WINNER triplica em ~2 anos; FLAT fica de lado.
    rows = []
    for i, (d, w, f) in enumerate([
        ("2020-06-30", 100, 100), ("2021-06-30", 200, 105), ("2022-06-30", 320, 110)]):
        rows.append({"symbol": "WIN", "month_end": d, "adjusted_close": w})
        rows.append({"symbol": "FLAT", "month_end": d, "adjusted_close": f})
    return pd.DataFrame(rows)


def test_multibagger_labels():
    labels = ob.multibagger_labels(_monthly(), "2020-06-30",
                                   horizon_years=5, multiple=3.0)
    assert labels["WIN"] is True       # 100 → 320 (>3×)
    assert labels["FLAT"] is False


def test_forward_total_return():
    fwd = ob.forward_total_return(_monthly(), "2020-06-30", horizon_years=5)
    assert fwd["WIN"] == pytest.approx(2.2)    # 320/100 - 1
    assert fwd["FLAT"] == pytest.approx(0.10)


def test_precision_recall():
    r = ob.precision_recall(predicted={"A", "B", "C"}, actual={"A", "B", "D"},
                            universe={"A", "B", "C", "D", "E"})
    assert r["true_positives"] == 2
    assert r["false_positives"] == 1
    assert r["false_negatives"] == 1
    assert r["precision"] == pytest.approx(2 / 3)
    assert r["recall"] == pytest.approx(2 / 3)
    assert r["base_rate"] == pytest.approx(3 / 5)
    assert r["lift_vs_random"] == pytest.approx((2 / 3) / (3 / 5))


def test_return_distribution():
    d = ob.return_distribution([-0.5, 0.0, 0.2, 2.0, 9.0])
    assert d["n"] == 5 and d["max"] == 9.0 and d["min"] == -0.5
    assert d["pct_positive"] == pytest.approx(0.6)


def test_top_winner_contribution():
    # uma vencedora enorme domina a riqueza terminal
    contrib = ob.top_winner_contribution([9.0, 0.0, -0.5, 0.1], k=1)
    assert contrib is not None and contrib > 0.6


def test_basket_return_com_zeros():
    rets = [9.0, 1.0, 0.0, -0.3, -0.5]        # uma multi-bagger + perdas
    full = ob.basket_return(rets, zero_fraction=0.0)
    with_zeros = ob.basket_return(rets, zero_fraction=0.4)   # 2 piores viram -100%
    assert full > with_zeros                  # zerar posições reduz o retorno
    assert with_zeros > -1.0                   # mas a vencedora ainda sustenta a cesta


def test_vazios():
    assert ob.multibagger_labels(pd.DataFrame(), "2020-06-30") == {}
    assert ob.return_distribution([])["n"] == 0
    assert ob.top_winner_contribution([]) is None
