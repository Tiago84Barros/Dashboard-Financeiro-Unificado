"""Adapta a composição parcial ao painel FII existente, sem alterar alocação."""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict

import numpy as np


def pvp_da_carteira(posicoes: Iterable[tuple[object, object]]) -> float | None:
    """P/VP agregado da carteira: média HARMÔNICA ponderada pelo peso.

    O P/VP da carteira é valor de mercado total sobre valor patrimonial total.
    Com peso ``w`` em mercado, a parcela patrimonial de cada fundo é
    ``w / pvp``; somando, ``P/VP = Σw / Σ(w/pvp)``. A média aritmética
    ``Σ(w·pvp)/Σw`` superestima: 50/50 em P/VP 0,5 e 1,5 dava 1,0, e o correto
    é 0,75 -- a carteira compra patrimônio com 25% de desconto, não pelo valor
    de face.

    P/VP ausente, não finito ou não positivo fica de fora e o peso se
    renormaliza sobre quem foi medido. Zero no lugar do ausente (o Dashboard
    Geral fazia ``pvp or 0``) puxava o agregado para baixo sem ninguém medir.
    """
    medidos = []
    for pvp, peso in posicoes:
        try:
            p, w = float(pvp), float(peso)
        except (TypeError, ValueError):
            continue
        if math.isfinite(p) and math.isfinite(w) and p > 0 and w > 0:
            medidos.append((p, w))
    if not medidos:
        return None
    return sum(w for _, w in medidos) / sum(w / p for p, w in medidos)


def enrich_review_presentation(proposal, policy, correlation_matrix=None):
    from core.fii_portfolio_v4 import (
        SCENARIOS,
        _correlation_risk_matrix,
        _resumo_de_renda,
    )
    from core.fii_scenarios import asset_scenario_return

    items = [dict(row) for row in proposal["items"]]
    total = sum(row["weight"] for row in items)
    if total <= 0:
        raise ValueError("Painel detalhado requer ao menos uma posição")
    relative = np.array([row["weight"]/total for row in items])
    # Apenas os indicadores da parcela investida usam pesos relativos.
    # Os pesos executáveis, no contrato de saída, continuam sobre o capital total.
    income_summary = _resumo_de_renda([
        {**row, "weight": float(weight)} for row, weight in zip(items, relative)])
    dy = np.array([row.get("dy_12m") if row.get("dy_12m") is not None else np.nan
                   for row in items], dtype=float)
    dy = np.where(dy > 1, dy/100, dy)
    income = float(relative @ dy) if np.isfinite(dy).all() else None
    _, correlation_info = _correlation_risk_matrix(items, correlation_matrix)
    return {**proposal, **income_summary, "items": items, "is_partial_review": True,
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
