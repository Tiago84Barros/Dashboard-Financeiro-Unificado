"""Run deterministic checks against the synthetic financial fixture."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic_financial_data.json"


def savings_rate(income: float, expenses: float) -> float:
    if income <= 0:
        raise ValueError("income must be positive")
    return (income - expenses) / income


def net_worth(assets: list[float], liabilities: list[float]) -> float:
    return sum(assets) - sum(liabilities)


def reserve_months(reserve: float, essential_monthly_expenses: float) -> float:
    if essential_monthly_expenses <= 0:
        raise ValueError("essential expenses must be positive")
    return reserve / essential_monthly_expenses


if __name__ == "__main__":
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    checks = payload["deterministic_checks"]
    assert abs(savings_rate(checks["income"], checks["expenses"]) - checks["expected_savings_rate"]) < 1e-12
    assert net_worth(checks["assets"], checks["liabilities"]) == checks["expected_net_worth"]
    assert reserve_months(checks["reserve"], checks["essential_monthly_expenses"]) == checks["expected_reserve_months"]
    print("OK: savings rate, net worth, and reserve coverage match the synthetic fixture.")
