import pandas as pd
import pytest

from core.fii_validation import (bootstrap_mean_ci, point_in_time_backtest,
                                 portfolio_turnover,
                                 robust_optimizer_point_in_time_backtest,
                                 validate_methodology, validation_supports_strategy)


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


def test_passed_validation_cannot_release_a_different_live_strategy():
    validation = {
        "status": "passed",
        "metrics": {"strategy_id": "fii_rank_equal_weight_buffered.v1"},
    }

    assert not validation_supports_strategy(
        validation, "fii_integrated_robust_optimizer.v6.6"
    )
    assert validation_supports_strategy(
        validation, "fii_rank_equal_weight_buffered.v1"
    )


def test_robust_optimizer_backtest_uses_v63_weights_without_constraint_violations():
    snapshots = []
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    for decision_index, decision in enumerate(("2025-01-31", "2025-02-28")):
        for index, fii_type in enumerate(types):
            row = {
                "ticker": f"F{index:03d}11", "tipo": fii_type,
                "type_score": 80 - index + decision_index, "confidence": .90,
                "coverage": .95, "dy_12m": .10, "pvp": .90,
                "liquidez_diaria": 3_000_000, "history_months": 36,
                "max_drawdown": -.15, "duration_anos": 3.0, "leverage": .05,
                "vacancia_fisica": .05, "delinquency": .01, "ltv": .55,
                "manager": f"manager-{index}", "sector": f"sector-{index}",
            }
            if fii_type in {"tijolo", "hibrido"}:
                row.update(
                    tenants={f"tenant-{index}": 1.0},
                    regions={f"region-{index}": 1.0},
                )
            if fii_type in {"papel", "hibrido"}:
                row.update(
                    debtors={f"debtor-{index}": 1.0},
                    issuers={f"issuer-{index}": 1.0},
                    indexers={f"indexer-{index}": 1.0},
                )
            snapshots.append({
                "reference_date": decision, "available_at": decision,
                "ticker": row["ticker"], "fii_type": fii_type,
                "score": row["type_score"], "confidence": row["confidence"],
                "coverage": row["coverage"],
                "availability_quality": "verified_publication",
                "portfolio_input_json": row,
            })
    return_dates = pd.date_range("2023-01-31", "2025-03-31", freq="ME")
    returns = pd.DataFrame([
            {
                "date": date, "ticker": f"F{index:03d}11",
                "total_return": .006 + index / 100_000 + (date.month % 3) / 1_000_000,
            }
        for date in return_dates for index in range(12)
    ])
    benchmark = pd.Series(.005, index=return_dates)
    scenarios = {
        "2025-01-31": {"selic": 12.0, "ipca": 4.5},
        "2025-02-28": {"selic": 12.0, "ipca": 4.5},
    }

    result = robust_optimizer_point_in_time_backtest(
        pd.DataFrame(snapshots), returns, benchmark, scenarios,
        transaction_cost=0, slippage=0,
    )

    assert result["strategy_id"] == "fii_integrated_robust_optimizer.v6.6"
    assert result["status"] == "calculated", result
    assert result["periods"] == 2
    assert result["constraint_violation_periods"] == 0
    assert result["optimizer_feasible_fraction"] == 1.0
    assert result["optimizer_input_ready_fraction"] == 1.0
    for observation in result["observations"]:
        assert sum(observation["holdings"].values()) == pytest.approx(1.0)
        assert max(observation["holdings"].values()) <= .15 + 1e-6
