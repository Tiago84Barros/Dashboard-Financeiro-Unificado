"""Monta a carteira FII atual, local ou remota, sem persistir pesos."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remote", action="store_true",
        help="usa o snapshot publicado e o gate remoto servidos pelo App4",
    )
    args = parser.parse_args()

    from core import market_read
    from core.config import settings
    from core.database import get_engine, get_session_factory
    from core.fii_integrated_model import (
        IntegratedEligibilityPolicy,
        apply_integrated_eligibility,
    )
    from core.fii_methodology import (
        MacroScenario,
        evaluate_publication_gate,
        score_fiis_by_type,
    )
    from core.fii_portfolio_v4 import (
        LIVE_PORTFOLIO_STRATEGY_ID,
        PortfolioPolicy,
        optimize_diligence_portfolio,
    )
    from core.fii_validation import validation_supports_strategy
    from scripts.publish_fii_selection_from_local import _warehouse_url

    if not args.remote:
        local_url = _warehouse_url()
        settings.SUPABASE_UNIFICADO_URL = local_url
        settings.DATABASE_URL = local_url
        settings.SUPABASE_DB_URL = local_url
    get_engine.clear()
    get_session_factory.clear()
    market_read._engine.clear()

    inputs = market_read.load_fii_methodology_inputs(prefer_snapshot=args.remote)
    eligible, eligibility = apply_integrated_eligibility(
        inputs.to_dict("records") if not inputs.empty else [],
        IntegratedEligibilityPolicy(),
    )
    validation = market_read.load_fii_validation_status()
    validation_status = (
        "passed"
        if validation_supports_strategy(validation, LIVE_PORTFOLIO_STRATEGY_ID)
        else "unvalidated"
    )
    scored = score_fiis_by_type(eligible, validation_status=validation_status)
    snapshot_dates: list[date] = []
    if "snapshot_metadata" in inputs.columns:
        for metadata in inputs["snapshot_metadata"]:
            if isinstance(metadata, dict) and metadata.get("as_of_date"):
                try:
                    snapshot_dates.append(date.fromisoformat(
                        str(metadata["as_of_date"])[:10]
                    ))
                except ValueError:
                    pass
    publication_gate = evaluate_publication_gate(
        scored,
        expected_universe=len(eligible),
        validation_status=validation_status,
        snapshot_as_of=max(snapshot_dates) if snapshot_dates else None,
    )
    candidates: list[str] = []
    for fii_type in ("tijolo", "papel", "fof", "hibrido"):
        candidates.extend([
            str(row["ticker"]) for row in scored if row.get("tipo") == fii_type
        ][:12])
    candidates = list(dict.fromkeys(candidates))
    prices = market_read.load_precos_mensais(tuple(sorted(candidates)))
    returns = prices.pct_change(fill_method=None) if not prices.empty else prices
    usable = [
        ticker for ticker in candidates
        if ticker in returns.columns and int(returns[ticker].notna().sum()) >= 12
    ]
    correlation = (
        returns[usable].corr(min_periods=12).to_dict() if len(usable) >= 2 else None
    )
    result = optimize_diligence_portfolio(
        scored,
        MacroScenario(
            selic=15.0,
            ipca=4.5,
            selic_change_12m=0.0,
            vacancy_shock=.08,
            credit_event_rate=.03,
        ),
        policy=PortfolioPolicy(),
        correlation_matrix=correlation,
        correlation_penalty=.12,
    )
    summary = {
        "universe_count": eligibility.get("universe_count"),
        "eligible_count": eligibility.get("eligible_count"),
        "status": result.get("status"),
        "can_publish": result.get("can_publish"),
        "publication_gate": {
            "can_publish": publication_gate.can_publish_recommendation,
            "reasons": list(publication_gate.reasons),
            "validation_status": validation_status,
        },
        "portfolio_can_publish": bool(
            result.get("can_publish")
            and publication_gate.can_publish_recommendation
        ),
        "blockers": result.get("blockers") or [],
        "items": [
            {
                "ticker": item.get("ticker"),
                "tipo": item.get("tipo"),
                "weight": item.get("weight"),
                "score": item.get("type_score"),
                "confidence": item.get("confidence"),
            }
            for item in result.get("items") or []
        ],
        "dimension_coverage": result.get("dimension_coverage") or {},
        "unresolved_dimensions": result.get("unresolved_dimensions") or [],
        "weighted_uncertainty": result.get("weighted_uncertainty"),
        "correlation_info": result.get("correlation_info") or {},
        "constraint_violations": result.get("constraint_violations") or [],
        "candidate_pool": result.get("candidate_pool") or {},
    }
    print(json.dumps(summary, ensure_ascii=False, default=str, sort_keys=True))
    return 0 if result.get("items") and (
        not args.remote or summary["portfolio_can_publish"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
