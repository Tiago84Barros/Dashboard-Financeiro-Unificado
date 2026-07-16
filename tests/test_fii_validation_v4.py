import pandas as pd
import pytest

from core.fii_validation import (bootstrap_mean_ci, point_in_time_backtest,
                                 portfolio_turnover, validate_methodology)


def test_bootstrap_is_reproducible_and_turnover_is_two_way_adjusted():
    assert bootstrap_mean_ci([.01, .02, -.01], seed=7) == bootstrap_mean_ci([.01, .02, -.01], seed=7)
    assert portfolio_turnover(pd.Series({"A": .5, "B": .5}),
                              pd.Series({"A": .25, "C": .75})) == .75


def test_pit_backtest_rejects_information_not_yet_available():
    snapshots = pd.DataFrame([
        {"reference_date": "2024-01-31", "available_at": "2024-01-30", "ticker": "A", "score": 90},
        {"reference_date": "2024-01-31", "available_at": "2024-04-02", "ticker": "B", "score": 100},
        {"reference_date": "2024-02-29", "available_at": "2024-02-28", "ticker": "A", "score": 80},
    ])
    returns = pd.DataFrame([
        {"date": "2024-01-31", "ticker": "A", "total_return": .02},
        {"date": "2024-01-31", "ticker": "B", "total_return": .50},
        {"date": "2024-02-29", "ticker": "A", "total_return": .01},
        {"date": "2024-03-01", "ticker": "A", "total_return": .02},
    ])
    benchmark = pd.Series([.0, .0, .0],
                          index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-01"]))
    result = point_in_time_backtest(snapshots, returns, benchmark, top_n=1,
                                    transaction_cost=0, slippage=0)
    assert result["periods"] == 2
    assert result["mean_return"] == pytest.approx(.015)
    assert result["observations"][0]["date"] > result["observations"][0]["decision_date"]


def test_validation_stays_blocked_without_history_and_regimes():
    result = validate_methodology({"status": "blocked"}, {})
    assert result["status"] == "blocked"
    assert result["blockers"]


def test_pit_backtest_rank_buffer_reduces_noise_turnover():
    snapshots = pd.DataFrame([
        {"reference_date": "2024-01-31", "available_at": "2024-01-31", "ticker": "A", "score": 100},
        {"reference_date": "2024-01-31", "available_at": "2024-01-31", "ticker": "B", "score": 90},
        {"reference_date": "2024-02-29", "available_at": "2024-02-29", "ticker": "A", "score": 89},
        {"reference_date": "2024-02-29", "available_at": "2024-02-29", "ticker": "B", "score": 90},
    ])
    returns = pd.DataFrame([
        {"date": "2024-02-29", "ticker": "A", "total_return": .01},
        {"date": "2024-02-29", "ticker": "B", "total_return": .01},
        {"date": "2024-03-31", "ticker": "A", "total_return": .01},
        {"date": "2024-03-31", "ticker": "B", "total_return": .01},
    ])
    benchmark = pd.Series([0.0, 0.0], index=pd.to_datetime(["2024-02-29", "2024-03-31"]))
    buffered = point_in_time_backtest(
        snapshots, returns, benchmark, top_n=1, rank_buffer=1,
        transaction_cost=0, slippage=0,
    )
    assert buffered["observations"][1]["holdings"] == {"A": 1.0}
    assert buffered["observations"][1]["turnover"] == 0.0
