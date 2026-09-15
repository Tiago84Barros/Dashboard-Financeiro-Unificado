"""Adapta a composição parcial ao painel FII existente, sem alterar alocação."""
from __future__ import annotations

from dataclasses import asdict

import numpy as np


def enrich_review_presentation(proposal, policy, correlation_matrix=None):
    from core.fii_portfolio_v4 import SCENARIOS, _correlation_risk_matrix
    from core.fii_scenarios import asset_scenario_return

    items = [dict(row) for row in proposal["items"]]
    total = sum(row["weight"] for row in items)
    if total <= 0:
        raise ValueError("Painel detalhado requer ao menos uma posição")
    relative = np.array([row["weight"]/total for row in items])
    dy = np.array([row.get("dy_12m") if row.get("dy_12m") is not None else np.nan
                   for row in items], dtype=float)
    dy = np.where(dy > 1, dy/100, dy)
    income = float(relative @ dy) if np.isfinite(dy).all() else None
    _, correlation_info = _correlation_risk_matrix(items, correlation_matrix)
    return {**proposal, "items": items, "is_partial_review": True,
            "can_publish": False, "blockers": list(proposal.get("reasons") or []),
            "trailing_yield_12m": income, "expected_yield": income,
            "effective_assets": float(1/(relative @ relative)),
            "scenario_returns": {
                name: float(sum(weight*asset_scenario_return(row, name)
                                for row, weight in zip(items, relative)))
                for name in SCENARIOS},
            "correlation_info": correlation_info, "correlation_risk": None,
            "policy": asdict(policy), "macro_mode": "fundamental",
            "metrics_scope": "parcela investida; saldo não alocado sem retorno presumido"}
