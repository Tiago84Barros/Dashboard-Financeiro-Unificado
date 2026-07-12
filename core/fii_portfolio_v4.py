"""Construção auditável da carteira de diligência de FIIs.

A otimização só impõe limites para dimensões cuja cobertura seja suficiente.
Dimensões críticas sem look-through são reportadas como bloqueios; nunca se
presume concentração zero por ausência de dados.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np

from core.fii_methodology import MacroScenario, tactical_type_bands, type_scenario_return


SCENARIOS = ("base", "selic_alta", "queda_selic", "inflacao_alta", "vacancia", "credito")


@dataclass(frozen=True)
class PortfolioPolicy:
    max_asset: float = .15
    max_manager: float = .25
    max_sector: float = .25
    max_tenant: float = .15
    max_debtor: float = .10
    max_issuer: float = .10
    max_indexer: float = .40
    max_region: float = .35
    max_illiquid: float = .10
    min_daily_liquidity: float = 1_000_000.0
    min_dimension_coverage: float = .80
    max_assets: int = 12


LIMITS = {
    "manager": "max_manager", "sector": "max_sector", "tenant": "max_tenant",
    "debtor": "max_debtor", "issuer": "max_issuer", "indexer": "max_indexer",
    "region": "max_region",
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _exposure(row: dict, dimension: str) -> dict[str, float]:
    plural = {"tenant": "tenants", "debtor": "debtors", "issuer": "issuers",
              "indexer": "indexers", "region": "regions"}.get(dimension)
    if plural:
        raw = row.get(plural)
        if not isinstance(raw, dict):
            return {}
        return {str(k): _num(v) for k, v in raw.items() if _num(v) > 0}
    value = row.get(dimension)
    return {str(value): 1.0} if value not in (None, "") else {}


def _dimension_matrix(rows: list[dict], dimension: str) -> tuple[np.ndarray, list[str], float]:
    observed = [_exposure(row, dimension) for row in rows]
    applicable_types = {
        "tenant": {"tijolo", "hibrido"}, "region": {"tijolo", "hibrido"},
        "debtor": {"papel", "hibrido"}, "issuer": {"papel", "hibrido"},
        "indexer": {"papel", "hibrido"},
    }.get(dimension)
    applicable = [i for i, row in enumerate(rows)
                  if applicable_types is None or str(row.get("tipo")) in applicable_types]
    coverage = (sum(bool(observed[i]) for i in applicable) / len(applicable)) if applicable else 0.0
    labels = sorted({key for item in observed for key in item})
    matrix = np.array([[item.get(label, 0.0) for label in labels] for item in observed], dtype=float)
    return matrix, labels, coverage


def optimize_diligence_portfolio(
    scored_rows: Iterable[dict], scenario: MacroScenario, *, policy: PortfolioPolicy | None = None,
) -> dict[str, Any]:
    """Maximiza qualidade/confiança e penaliza perdas de cenários adversos."""
    policy = policy or PortfolioPolicy()
    bands = tactical_type_bands(scenario)
    ranked = sorted((dict(r) for r in scored_rows if _num(r.get("confidence")) > 0),
                    key=lambda r: (_num(r.get("type_score")), _num(r.get("confidence"))), reverse=True)
    rows: list[dict] = []
    # Reserva candidatos suficientes para cumprir a banda mínima com o teto por ativo.
    for fii_type in ("tijolo", "papel", "fof", "hibrido"):
        required = max(1, int(np.ceil(bands[fii_type][0] / policy.max_asset)))
        rows.extend([row for row in ranked if str(row.get("tipo")) == fii_type][:required])
    rows.extend(row for row in ranked if row not in rows)
    rows = rows[:policy.max_assets]
    if not rows:
        return {"items": [], "status": "blocked", "can_publish": False,
                "blockers": ["nenhum candidato com dados utilizáveis"]}

    n = len(rows)
    quality = np.array([_num(r.get("type_score")) / 100 for r in rows])
    confidence = np.array([_num(r.get("confidence")) for r in rows])
    dy = np.array([_num(r.get("dy_12m")) for r in rows])
    if np.nanmax(dy, initial=0) > 1.0:
        dy = dy / 100.0
    adverse = np.array([
        np.mean([max(-type_scenario_return(str(r.get("tipo")), s), 0) for s in SCENARIOS[1:]])
        for r in rows
    ])
    utility = .45 * quality + .30 * confidence + .25 * np.clip(dy / .15, 0, 1) - .35 * adverse

    constraints: list[dict] = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    unresolved: list[str] = []
    dimension_info: dict[str, dict] = {}
    for dimension, limit_attr in LIMITS.items():
        matrix, labels, coverage = _dimension_matrix(rows, dimension)
        limit = getattr(policy, limit_attr)
        dimension_info[dimension] = {"coverage": coverage, "labels": labels, "limit": limit}
        if coverage < policy.min_dimension_coverage:
            unresolved.append(dimension)
            continue
        for column in range(len(labels)):
            exposure = matrix[:, column].copy()
            constraints.append({"type": "ineq", "fun": lambda w, e=exposure, lim=limit: lim - float(w @ e)})

    for fii_type, (lower, upper) in bands.items():
        mask = np.array([1.0 if str(row.get("tipo")) == fii_type else 0.0 for row in rows])
        if mask.sum() == 0:
            unresolved.append(f"categoria:{fii_type}")
            continue
        constraints.extend([
            {"type": "ineq", "fun": lambda w, m=mask, lo=lower: float(w @ m) - lo},
            {"type": "ineq", "fun": lambda w, m=mask, hi=upper: hi - float(w @ m)},
        ])

    illiquid = np.array([1.0 if _num(r.get("liquidez_diaria")) < policy.min_daily_liquidity else 0.0 for r in rows])
    constraints.append({"type": "ineq", "fun": lambda w: policy.max_illiquid - float(w @ illiquid)})

    def objective(weights: np.ndarray) -> float:
        concentration = float(np.sum(weights ** 2))
        scenario_values = [sum(weights[i] * type_scenario_return(str(rows[i].get("tipo")), s)
                               for i in range(n)) for s in SCENARIOS[1:]]
        tail_loss = -min(scenario_values)
        return -float(weights @ utility) + .18 * concentration + .30 * max(tail_loss, 0)

    try:
        from scipy.optimize import minimize
        result = minimize(objective, np.full(n, 1 / n), method="SLSQP",
                          bounds=[(0.0, policy.max_asset)] * n, constraints=constraints,
                          options={"maxiter": 1000, "ftol": 1e-10})
    except Exception as exc:  # pragma: no cover - ambiente sem scipy
        return {"items": [], "status": "blocked", "can_publish": False,
                "blockers": [f"otimizador indisponível: {exc}"], "unresolved_dimensions": unresolved}

    if not result.success:
        return {"items": [], "status": "blocked", "can_publish": False,
                "blockers": [f"otimização inviável: {result.message}"],
                "unresolved_dimensions": sorted(set(unresolved)), "policy": asdict(policy)}

    weights = np.where(result.x >= .005, result.x, 0.0)
    weights = weights / weights.sum()
    items = [{**rows[i], "weight": round(float(weights[i]), 6)} for i in range(n) if weights[i] > 0]
    scenario_returns = {s: round(sum(item["weight"] * type_scenario_return(str(item.get("tipo")), s)
                                     for item in items), 6) for s in SCENARIOS}
    validation_block = any(item.get("publication_status") != "validated" for item in items)
    blockers = []
    if unresolved:
        blockers.append("exposições sem cobertura suficiente: " + ", ".join(sorted(set(unresolved))))
    if validation_block:
        blockers.append("há ativos/metodologia ainda classificados somente para diligência")
    return {
        "items": items, "status": "publishable" if not blockers else "diligence_only",
        "can_publish": not blockers, "blockers": blockers,
        "unresolved_dimensions": sorted(set(unresolved)), "dimension_coverage": dimension_info,
        "scenario_returns": scenario_returns,
        "expected_yield": round(sum(item["weight"] * (_num(item.get("dy_12m")) / (100 if _num(item.get("dy_12m")) > 1 else 1)) for item in items), 6),
        "effective_assets": round(1 / sum(item["weight"] ** 2 for item in items), 2),
        "macro_bands": bands, "policy": asdict(policy), "solver": str(result.message),
    }


def evaluate_rebalance_triggers(current: dict, proposed: dict, *, costs: float = .0025) -> list[str]:
    """Rebalanceamento por evento; a passagem do tempo isoladamente não dispara ação."""
    reasons: list[str] = []
    if current.get("macro_regime") != proposed.get("macro_regime"):
        reasons.append("mudança material de regime macroeconômico")
    if proposed.get("material_event"):
        reasons.append("evento relevante de crédito, imóvel, gestão ou CVM")
    if proposed.get("constraint_breach"):
        reasons.append("limite de concentração ou liquidez violado")
    income_gain = _num(proposed.get("expected_yield")) - _num(current.get("expected_yield"))
    if income_gain > costs + .005:
        reasons.append("ganho de renda esperado supera custos e margem mínima")
    if _num(current.get("stress_loss")) - _num(proposed.get("stress_loss")) >= .02:
        reasons.append("redução material da perda em cenário de estresse")
    if _num(current.get("confidence")) - _num(proposed.get("confidence")) >= .15:
        reasons.append("deterioração relevante da confiança dos dados")
    return reasons
