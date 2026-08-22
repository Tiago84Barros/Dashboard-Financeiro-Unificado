"""Executa e persiste no warehouse local o walk-forward do otimizador FII v6.7."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=12)
    args = parser.parse_args()

    from core.config import settings
    from core.database import get_engine, get_session_factory
    from data_pipeline.market.fii_pit import run_pit_validation
    from scripts.publish_fii_selection_from_local import _warehouse_url

    local_url = _warehouse_url()
    settings.SUPABASE_UNIFICADO_URL = local_url
    settings.DATABASE_URL = local_url
    settings.SUPABASE_DB_URL = local_url
    get_engine.clear()
    get_session_factory.clear()
    result = run_pit_validation(years=max(args.years, 1), top_n=max(args.top_n, 1))
    metrics = result.get("metrics") or {}
    summary = {
        "status": result.get("status"),
        "validation_run_id": result.get("validation_run_id"),
        "backtest_run_id": result.get("backtest_run_id"),
        "snapshots": result.get("snapshots"),
        "periods": result.get("periods"),
        "benchmark": result.get("benchmark"),
        "strategy_id": metrics.get("strategy_id"),
        "mean_return": metrics.get("mean_return"),
        "mean_benchmark": metrics.get("mean_benchmark"),
        "mean_excess": metrics.get("mean_excess"),
        "max_drawdown": metrics.get("max_drawdown"),
        "annualized_turnover": metrics.get("annualized_turnover"),
        "optimizer_feasible_fraction": metrics.get("optimizer_feasible_fraction"),
        "constraint_violation_periods": metrics.get("constraint_violation_periods"),
        "mean_correlation_coverage": metrics.get("mean_correlation_coverage"),
        "blockers": result.get("blockers") or [],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"passed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
