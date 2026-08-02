"""Construção auditável da carteira de diligência de FIIs.

A otimização só impõe limites para dimensões cuja cobertura seja suficiente.
Dimensões críticas sem look-through são reportadas como bloqueios; nunca se
presume concentração zero por ausência de dados.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np

from core.fii_lookthrough import (
    dimension_is_applicable,
    normalized_dimension_mapping,
    supplementary_evidence_score,
)
from core.fii_methodology import MacroScenario, tactical_type_bands
from core.fii_scenarios import asset_scenario_return


SCENARIOS = ("base", "selic_alta", "queda_selic", "inflacao_alta", "vacancia", "credito")
LIVE_PORTFOLIO_STRATEGY_ID = "fii_integrated_robust_optimizer.v6.6"


@dataclass(frozen=True)
class PortfolioPolicy:
    max_asset: float = .15
    min_asset_weight: float = .02
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
    min_correlation_coverage: float = .80
    max_assets: int = 12
    uncertainty_penalty: float = .20
    cvar_penalty: float = .35
    turnover_penalty: float = .02
    # Preferência pequena por evidência nominal suplementar observada. Não
    # substitui score, confiança, renda nem os gates de cobertura.
    data_quality_bonus: float = .06
    min_distinct_types: int = 2
    max_single_type: float = .70
    # Dimensões cuja identidade tem histórico PIT verificável nas fontes
    # estruturadas. Gestor só possui primeira observação em 2026 no warehouse;
    # continua limitado na carteira atual quando coberto, mas não pode ser
    # exigido retroativamente sem introduzir look-ahead.
    required_dimensions: tuple[str, ...] = ("sector", "issuer")
    # Limite de incerteza da carteira de diligência. O antigo .30 exigia
    # confiança ponderada >=70% e tornava o LP inviável no universo real,
    # embora houvesse candidatos suficientes. O gate de publicação continua
    # separado e bloqueia a recomendação enquanto a validação PIT não passar.
    max_weighted_uncertainty: float = .35


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


def _adaptive_type_bands(
    bands: dict[str, tuple[float, float]],
    rows: list[dict],
    policy: PortfolioPolicy,
) -> tuple[dict[str, tuple[float, float]], dict[str, Any]]:
    """Adapta bandas somente quando uma categoria não tem ativo elegível.

    A ausência não autoriza incluir um fundo reprovado. O piso da categoria
    indisponível vira zero e a capacidade necessária para somar 100% é
    distribuída entre categorias presentes, sem superar ``max_single_type``.
    """
    available_counts = {
        fii_type: sum(str(row.get("tipo")) == fii_type for row in rows)
        for fii_type in bands
    }
    available = [fii_type for fii_type, count in available_counts.items() if count > 0]
    if len(available) < policy.min_distinct_types:
        return {}, {
            "status": "blocked",
            "reason": (
                f"menos de {policy.min_distinct_types} categorias elegíveis"
            ),
            "available_by_type": available_counts,
        }

    effective = {
        fii_type: (
            (float(lower), min(float(upper), policy.max_single_type))
            if available_counts[fii_type] > 0 else (0.0, 0.0)
        )
        for fii_type, (lower, upper) in bands.items()
    }
    capacity_gap = max(1.0 - sum(upper for _, upper in effective.values()), 0.0)
    while capacity_gap > 1e-12:
        candidates = [
            fii_type for fii_type in available
            if effective[fii_type][1] < policy.max_single_type - 1e-12
        ]
        if not candidates:
            return {}, {
                "status": "blocked",
                "reason": "bandas adaptativas não comportam 100% da carteira",
                "available_by_type": available_counts,
            }
        share = capacity_gap / len(candidates)
        allocated = 0.0
        for fii_type in candidates:
            lower, upper = effective[fii_type]
            increment = min(share, policy.max_single_type - upper)
            effective[fii_type] = (lower, upper + increment)
            allocated += increment
        if allocated <= 1e-12:
            break
        capacity_gap = max(capacity_gap - allocated, 0.0)

    return effective, {
        "status": "adapted" if any(available_counts[key] == 0 for key in bands) else "original",
        "available_by_type": available_counts,
        "unavailable_types": sorted(
            key for key, count in available_counts.items() if count == 0
        ),
        "original_bands": bands,
        "effective_bands": effective,
    }


def _candidate_pool(ranked: list[dict], bands: dict[str, tuple[float, float]],
                    policy: PortfolioPolicy, scenario: MacroScenario,
                    previous_weights: dict[str, float] | None = None,
                    ) -> tuple[list[dict], dict[str, Any]]:
    """Seleciona até ``max_assets`` candidatos por programação inteira-mista.

    O pré-seletor precisa respeitar todos os limites que podem tornar a etapa
    contínua inviável. Em particular, descartar ativos de maior confiança antes
    de aplicar o teto de incerteza criava falsos negativos: o universo completo
    era factível, mas o subconjunto devolvido ao otimizador não era.
    """
    per_type = max(policy.max_assets, 12)
    pool: list[dict] = []
    for fii_type in ("tijolo", "papel", "fof", "hibrido"):
        pool.extend([row for row in ranked if str(row.get("tipo")) == fii_type][:per_type])
    pool.extend(row for row in ranked if row not in pool)
    pool = pool[:max(policy.max_assets * 6, 48)]
    if not pool:
        return [], {"status": "data_prerequisites_missing", "reason": "pool vazio"}

    n = len(pool)
    quality = np.array([_num(row.get("type_score")) / 100 for row in pool])
    confidence = np.array([_num(row.get("confidence")) for row in pool])
    dy = np.array([_num(row.get("dy_12m")) for row in pool])
    if np.nanmax(dy, initial=0) > 1:
        dy = dy / 100
    adverse = np.array([
        np.mean([max(-asset_scenario_return(row, name), 0) for name in SCENARIOS[1:]])
        for row in pool
    ])
    evidence = np.array([supplementary_evidence_score(row) for row in pool])
    utility = (
        .45 * quality
        + .30 * confidence
        + .25 * np.clip(dy / .15, 0, 1)
        + policy.data_quality_bonus * evidence
        - .35 * adverse
    )
    previous_weights = previous_weights or {}
    previous = np.array([
        max(_num(previous_weights.get(str(row.get("ticker")))), 0.0)
        for row in pool
    ])
    utility = utility + policy.turnover_penalty * np.clip(
        previous / max(policy.max_asset, 1e-12), 0.0, 1.0
    )

    matrix_rows: list[np.ndarray] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []

    def constraint(weights: np.ndarray, lower: float, upper: float) -> None:
        matrix_rows.append(np.concatenate([weights, np.zeros(n)]))
        lower_bounds.append(lower)
        upper_bounds.append(upper)

    constraint(np.ones(n), 1.0, 1.0)
    for fii_type, (lower, upper) in bands.items():
        mask = np.array([1.0 if str(row.get("tipo")) == fii_type else 0.0 for row in pool])
        constraint(mask, lower, upper)
    universe_dimension_coverage: dict[str, float] = {}
    for dimension, limit_attr in LIMITS.items():
        exposure, labels, coverage = _dimension_matrix(pool, dimension)
        universe_dimension_coverage[dimension] = coverage
        # A pré-seleção sempre respeita as exposições nominais que foram
        # observadas. A cobertura continua decidindo se o limite pode ser
        # declarado controlado no resultado final; ela não deve, porém, permitir
        # que o MILP escolha um subconjunto documentado que o solver contínuo
        # imediatamente reprovará.
        for column in range(len(labels)):
            constraint(exposure[:, column], -np.inf, getattr(policy, limit_attr))
    illiquid = np.array([
        1.0 if _num(row.get("liquidez_diaria")) < policy.min_daily_liquidity else 0.0
        for row in pool
    ])
    constraint(illiquid, -np.inf, policy.max_illiquid)
    uncertainty = 1.0 - np.clip(confidence, 0.0, 1.0)
    constraint(uncertainty, -np.inf, policy.max_weighted_uncertainty)

    # Ligação peso_i <= teto_ativo * selecionado_i.
    for index in range(n):
        row = np.zeros(2 * n)
        row[index] = 1.0
        row[n + index] = -policy.max_asset
        matrix_rows.append(row)
        lower_bounds.append(-np.inf)
        upper_bounds.append(0.0)
        row = np.zeros(2 * n)
        row[index] = 1.0
        row[n + index] = -policy.min_asset_weight
        matrix_rows.append(row)
        lower_bounds.append(0.0)
        upper_bounds.append(np.inf)

    for dimension in policy.required_dimensions:
        known, applicable = _dimension_observation_masks(pool, dimension)
        if not bool(applicable.any()):
            continue
        row = np.concatenate([
            np.zeros(n),
            policy.min_dimension_coverage * applicable - known,
        ])
        matrix_rows.append(row)
        lower_bounds.append(-np.inf)
        upper_bounds.append(0.0)
    cardinality = np.concatenate([np.zeros(n), np.ones(n)])
    matrix_rows.append(cardinality)
    lower_bounds.append(-np.inf)
    upper_bounds.append(float(policy.max_assets))

    try:
        from scipy.optimize import Bounds, LinearConstraint, milp
        objective = np.concatenate([-utility, np.full(n, 1e-6)])
        solution = milp(
            c=objective,
            integrality=np.concatenate([np.zeros(n), np.ones(n)]),
            bounds=Bounds(np.zeros(2 * n),
                          np.concatenate([np.full(n, policy.max_asset), np.ones(n)])),
            constraints=LinearConstraint(
                np.vstack(matrix_rows), np.array(lower_bounds), np.array(upper_bounds)),
            options={"time_limit": 20},
        )
        if solution.success and solution.x is not None:
            selected = [
                pool[index] for index, weight in enumerate(solution.x[:n])
                if weight >= policy.min_asset_weight - 1e-8
            ]
            selected_coverage = {
                dimension: _dimension_matrix(selected, dimension)[2]
                for dimension in LIMITS
            }
            return selected, {
                "status": "milp_feasible",
                "solver_message": str(solution.message),
                "selected_count": len(selected),
                "selected_weights": {
                    str(pool[index].get("ticker")): float(weight)
                    for index, weight in enumerate(solution.x[:n])
                    if weight >= policy.min_asset_weight - 1e-8
                },
                "universe_dimension_coverage": universe_dimension_coverage,
                "selected_dimension_coverage": selected_coverage,
            }
        return [], {
            "status": "data_prerequisites_missing",
            "reason": str(solution.message),
            "universe_dimension_coverage": universe_dimension_coverage,
        }
    except Exception as exc:
        fallback_reason = str(exc)

    # Fallback determinístico para ambientes sem solver MILP.
    selected: list[dict] = []

    def issuer_fit(row: dict) -> int:
        issuers = row.get("issuers")
        if not isinstance(issuers, dict) or not issuers:
            return 1
        maximum = max((_num(value) for value in issuers.values()), default=0.0)
        return int(maximum * policy.max_asset <= policy.max_issuer + 1e-9)

    def liquid(row: dict) -> int:
        return int(_num(row.get("liquidez_diaria")) >= policy.min_daily_liquidity)

    for fii_type in ("tijolo", "papel", "fof", "hibrido"):
        candidates = [row for row in ranked if str(row.get("tipo")) == fii_type]
        lower = bands[fii_type][0]
        required = max(1, int(np.ceil(lower / policy.max_asset)))
        sector_groups = max(1, int(np.ceil(lower / policy.max_sector)))
        manager_groups = max(1, int(np.ceil(lower / policy.max_manager)))
        chosen: list[dict] = []
        sectors: set[str] = set()
        managers: set[str] = set()
        while len(chosen) < required:
            available = [row for row in candidates if row not in chosen]
            if not available:
                break

            def priority(row: dict) -> tuple[int, int, int, int, float, float, float]:
                sector = str(row.get("sector") or "")
                manager = str(row.get("manager") or "")
                return (
                    issuer_fit(row), liquid(row),
                    int(bool(sector) and sector not in sectors
                        and len(sectors) < sector_groups),
                    int(bool(manager) and manager not in managers
                        and len(managers) < manager_groups),
                    supplementary_evidence_score(row),
                    _num(row.get("type_score")), _num(row.get("confidence")),
                )

            candidate = max(available, key=priority)
            chosen.append(candidate)
            if candidate.get("sector"):
                sectors.add(str(candidate["sector"]))
            if candidate.get("manager"):
                managers.add(str(candidate["manager"]))
        selected.extend(chosen)

    selected_sectors = {str(row.get("sector")) for row in selected if row.get("sector")}
    selected_managers = {str(row.get("manager")) for row in selected if row.get("manager")}
    remaining = [row for row in ranked if row not in selected]
    while len(selected) < policy.max_assets and remaining:
        candidate = max(remaining, key=lambda row: (
            issuer_fit(row), liquid(row),
            int(bool(row.get("manager")) and str(row.get("manager")) not in selected_managers),
            int(bool(row.get("sector")) and str(row.get("sector")) not in selected_sectors),
            supplementary_evidence_score(row),
            _num(row.get("type_score")), _num(row.get("confidence")),
        ))
        selected.append(candidate)
        remaining.remove(candidate)
        if candidate.get("sector"):
            selected_sectors.add(str(candidate["sector"]))
        if candidate.get("manager"):
            selected_managers.add(str(candidate["manager"]))
    selected = selected[:policy.max_assets]
    selected_coverage = {
        dimension: _dimension_matrix(selected, dimension)[2]
        for dimension in LIMITS
    }
    missing_required = [
        dimension for dimension in policy.required_dimensions
        if selected_coverage.get(dimension, 0.0) < policy.min_dimension_coverage
    ]
    if missing_required:
        return [], {
            "status": "data_prerequisites_missing",
            "reason": "fallback sem cobertura obrigatória: " + ", ".join(missing_required),
            "universe_dimension_coverage": universe_dimension_coverage,
            "selected_dimension_coverage": selected_coverage,
        }
    return selected, {
        "status": "deterministic_fallback",
        "reason": fallback_reason,
        "selected_count": len(selected),
        "universe_dimension_coverage": universe_dimension_coverage,
        "selected_dimension_coverage": selected_coverage,
    }


def _exposure(row: dict, dimension: str) -> dict[str, float]:
    plural = {"tenant": "tenants", "debtor": "debtors", "issuer": "issuers",
              "indexer": "indexers", "region": "regions"}.get(dimension)
    if plural:
        return normalized_dimension_mapping(row, dimension)
    value = row.get(dimension)
    return {str(value): 1.0} if value not in (None, "") else {}


def _dimension_observation_masks(
    rows: list[dict], dimension: str,
) -> tuple[np.ndarray, np.ndarray]:
    applicable = np.array([
        1.0 if dimension_is_applicable(row, dimension) else 0.0
        for row in rows
    ])
    known = np.array([
        1.0 if applicable[index] and bool(_exposure(row, dimension)) else 0.0
        for index, row in enumerate(rows)
    ])
    return known, applicable


def _dimension_matrix(rows: list[dict], dimension: str) -> tuple[np.ndarray, list[str], float]:
    observed = [_exposure(row, dimension) for row in rows]
    known, applicable = _dimension_observation_masks(rows, dimension)
    coverage = float(known.sum() / applicable.sum()) if applicable.sum() else 0.0
    labels = sorted({key for item in observed for key in item})
    matrix = np.array([[item.get(label, 0.0) for label in labels] for item in observed], dtype=float)
    return matrix, labels, coverage


def _correlation_risk_matrix(
    rows: list[dict], correlation_matrix: dict[str, dict[str, float]] | None,
) -> tuple[np.ndarray | None, dict[str, float]]:
    """Matriz PSD com fallback explícito apenas quando há pares observados."""
    n = len(rows)
    total_pairs = n * (n - 1) // 2
    if n < 2 or not correlation_matrix:
        return None, {"coverage": 0.0, "observed_pairs": 0, "total_pairs": total_pairs}
    tickers = [str(row.get("ticker") or "") for row in rows]
    observed: list[float] = []
    pairs: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            left = (correlation_matrix.get(tickers[i]) or {}).get(tickers[j])
            right = (correlation_matrix.get(tickers[j]) or {}).get(tickers[i])
            values = [_num(value, np.nan) for value in (left, right)]
            clean = [value for value in values if np.isfinite(value)]
            if clean:
                value = float(np.clip(np.mean(clean), -1.0, 1.0))
                pairs[(i, j)] = value
                observed.append(value)
    if not observed:
        return None, {"coverage": 0.0, "observed_pairs": 0, "total_pairs": total_pairs}

    fallback = float(np.median(observed))
    matrix = np.full((n, n), fallback, dtype=float)
    np.fill_diagonal(matrix, 1.0)
    for (i, j), value in pairs.items():
        matrix[i, j] = matrix[j, i] = value

    # Correlações par-a-par com históricos distintos podem não formar matriz
    # semidefinida positiva. A projeção evita um termo de risco não convexo.
    eigenvalues, eigenvectors = np.linalg.eigh((matrix + matrix.T) / 2.0)
    matrix = eigenvectors @ np.diag(np.clip(eigenvalues, 1e-8, None)) @ eigenvectors.T
    scale = np.sqrt(np.clip(np.diag(matrix), 1e-12, None))
    matrix = matrix / np.outer(scale, scale)
    np.fill_diagonal(matrix, 1.0)
    return matrix, {
        "coverage": len(observed) / total_pairs if total_pairs else 0.0,
        "observed_pairs": len(observed),
        "total_pairs": total_pairs,
        "fallback_correlation": fallback,
    }


def portfolio_constraint_violations(
    items: Iterable[dict], bands: dict[str, tuple[float, float]],
    policy: PortfolioPolicy, *, tolerance: float = 1e-6,
) -> tuple[str, ...]:
    """Revalida a carteira exatamente como será exibida e persistida."""
    rows = [dict(item) for item in items if _num(item.get("weight")) > 0]
    violations: list[str] = []
    total = sum(_num(row.get("weight")) for row in rows)
    if not rows or abs(total - 1.0) > tolerance:
        violations.append("pesos não somam 100%")
    if len(rows) > policy.max_assets:
        violations.append("quantidade de ativos acima do limite")
    if any(_num(row.get("weight")) > policy.max_asset + tolerance for row in rows):
        violations.append("peso individual acima do limite")
    if any(_num(row.get("weight")) < policy.min_asset_weight - tolerance for row in rows):
        violations.append("peso individual abaixo do mínimo econômico")
    for fii_type, (lower, upper) in bands.items():
        weight = sum(_num(row.get("weight")) for row in rows
                     if str(row.get("tipo")) == fii_type)
        if weight < lower - tolerance or weight > upper + tolerance:
            violations.append(f"banda da categoria {fii_type} violada")
    illiquid_weight = sum(
        _num(row.get("weight")) for row in rows
        if _num(row.get("liquidez_diaria")) < policy.min_daily_liquidity
    )
    if illiquid_weight > policy.max_illiquid + tolerance:
        violations.append("limite de ativos ilíquidos violado")
    uncertainty = sum(
        _num(row.get("weight")) * (1.0 - min(max(_num(row.get("confidence")), 0.0), 1.0))
        for row in rows
    )
    if uncertainty > policy.max_weighted_uncertainty + tolerance:
        violations.append("incerteza ponderada acima do limite")
    for dimension, limit_attr in LIMITS.items():
        matrix, labels, coverage = _dimension_matrix(rows, dimension)
        if coverage < policy.min_dimension_coverage:
            continue
        weights = np.array([_num(row.get("weight")) for row in rows])
        limit = float(getattr(policy, limit_attr))
        if any(float(weights @ matrix[:, column]) > limit + tolerance
               for column in range(len(labels))):
            violations.append(f"concentração por {dimension} acima do limite")
    return tuple(dict.fromkeys(violations))


def optimize_diligence_portfolio(
    scored_rows: Iterable[dict], scenario: MacroScenario, *, policy: PortfolioPolicy | None = None,
    correlation_matrix: dict[str, dict[str, float]] | None = None,
    correlation_penalty: float = 0.0,
    previous_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Maximiza qualidade/confiança e penaliza perdas de cenários adversos."""
    policy = policy or PortfolioPolicy()
    original_bands = tactical_type_bands(scenario)
    ranked = sorted((dict(r) for r in scored_rows if _num(r.get("confidence")) > 0),
                    key=lambda r: (_num(r.get("type_score")), _num(r.get("confidence"))), reverse=True)
    bands, band_adaptation = _adaptive_type_bands(original_bands, ranked, policy)
    available_by_type = band_adaptation.get("available_by_type") or {}
    if not bands:
        return {
            "items": [], "status": "blocked", "can_publish": False,
            "blockers": [str(band_adaptation.get("reason") or "bandas inviáveis")],
            "feasibility_diagnostics": band_adaptation,
            "failure_stage": "data_prerequisites",
        }
    rows, candidate_pool_info = _candidate_pool(
        ranked, bands, policy, scenario, previous_weights=previous_weights,
    )
    if not rows:
        universe_coverage = (
            candidate_pool_info.get("universe_dimension_coverage") or {}
        )
        unresolved_required = sorted(
            dimension for dimension in policy.required_dimensions
            if float(universe_coverage.get(dimension) or 0.0)
            < policy.min_dimension_coverage
        )
        return {"items": [], "status": "blocked", "can_publish": False,
                "blockers": [
                    "pré-seleção inviável sob bandas, cardinalidade, "
                    "liquidez, concentração e incerteza"
                ],
                "feasibility_diagnostics": {
                    "available_by_type": available_by_type,
                    "candidate_count": len(ranked),
                    "minimum_weighted_confidence":
                        1.0 - policy.max_weighted_uncertainty,
                    "candidate_pool": candidate_pool_info,
                },
                "unresolved_dimensions": unresolved_required,
                "unresolved_critical_dimensions": unresolved_required,
                "dimension_coverage": {
                    dimension: {"coverage": float(coverage)}
                    for dimension, coverage in universe_coverage.items()
                },
                "failure_stage": "data_prerequisites"}

    n = len(rows)
    quality = np.array([_num(r.get("type_score")) / 100 for r in rows])
    confidence = np.array([_num(r.get("confidence")) for r in rows])
    dy = np.array([_num(r.get("dy_12m")) for r in rows])
    if np.nanmax(dy, initial=0) > 1.0:
        dy = dy / 100.0
    scenario_values_by_asset = np.array([
        [asset_scenario_return(r, s) for s in SCENARIOS] for r in rows
    ], dtype=float)
    adverse = np.maximum(-scenario_values_by_asset[:, 1:], 0.0).mean(axis=1)
    evidence = np.array([supplementary_evidence_score(row) for row in rows])
    uncertainty = 1.0 - np.clip(confidence, 0.0, 1.0)
    normalized_previous = {
        str(ticker): max(_num(weight), 0.0)
        for ticker, weight in (previous_weights or {}).items()
    }
    previous_vector = np.array([
        normalized_previous.get(str(row.get("ticker")), 0.0) for row in rows
    ])
    previous_outside_pool = max(
        sum(normalized_previous.values()) - float(previous_vector.sum()), 0.0
    )
    utility = (
        .45 * quality
        + .30 * confidence
        + .25 * np.clip(dy / .15, 0, 1)
        + policy.data_quality_bonus * evidence
        - .35 * adverse
        - policy.uncertainty_penalty * uncertainty
    )
    correlation_risk, correlation_info = _correlation_risk_matrix(rows, correlation_matrix)

    constraints: list[dict] = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    linear_ub: list[tuple[np.ndarray, float]] = []
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
            linear_ub.append((exposure, limit))

    for fii_type, (lower, upper) in bands.items():
        mask = np.array([1.0 if str(row.get("tipo")) == fii_type else 0.0 for row in rows])
        if mask.sum() == 0:
            unresolved.append(f"categoria:{fii_type}")
            continue
        constraints.extend([
            {"type": "ineq", "fun": lambda w, m=mask, lo=lower: float(w @ m) - lo},
            {"type": "ineq", "fun": lambda w, m=mask, hi=upper: hi - float(w @ m)},
        ])
        linear_ub.extend([(-mask, -lower), (mask, upper)])

    illiquid = np.array([1.0 if _num(r.get("liquidez_diaria")) < policy.min_daily_liquidity else 0.0 for r in rows])
    constraints.append({"type": "ineq", "fun": lambda w: policy.max_illiquid - float(w @ illiquid)})
    linear_ub.append((illiquid, policy.max_illiquid))
    constraints.append({
        "type": "ineq",
        "fun": lambda w: policy.max_weighted_uncertainty - float(w @ uncertainty),
    })
    linear_ub.append((uncertainty, policy.max_weighted_uncertainty))

    def objective(weights: np.ndarray) -> float:
        concentration = float(np.sum(weights ** 2))
        scenario_values = weights @ scenario_values_by_asset[:, 1:]
        losses = np.maximum(-scenario_values, 0.0)
        tail_count = max(1, int(np.ceil(len(losses) * .40)))
        cvar_loss = float(np.sort(losses)[-tail_count:].mean())
        correlation_risk_value = (
            float(weights @ correlation_risk @ weights)
            if correlation_risk is not None else 0.0
        )
        turnover = .5 * (
            float(np.abs(weights - previous_vector).sum()) + previous_outside_pool
        )
        return (-float(weights @ utility) + .18 * concentration
                + policy.cvar_penalty * cvar_loss
                + max(float(correlation_penalty), 0.0) * correlation_risk_value
                + policy.turnover_penalty * turnover)

    try:
        from scipy.optimize import linprog, minimize
        # O ponto de pesos iguais frequentemente viola bandas ou o teto de
        # iliquidez. Primeiro encontra-se uma solução linear factível; depois o
        # SLSQP otimiza concentração e perdas de cauda a partir dela.
        feasible = linprog(
            c=-utility,
            A_ub=np.vstack([row for row, _ in linear_ub]) if linear_ub else None,
            b_ub=np.array([limit for _, limit in linear_ub]) if linear_ub else None,
            A_eq=np.ones((1, n)), b_eq=np.array([1.0]),
            bounds=[(policy.min_asset_weight, policy.max_asset)] * n, method="highs",
        )
        if not feasible.success:
            return {"items": [], "status": "blocked", "can_publish": False,
                    "blockers": [f"restrições lineares inviáveis: {feasible.message}"],
                    "unresolved_dimensions": sorted(set(unresolved)),
                    "dimension_coverage": dimension_info, "policy": asdict(policy),
                    "candidate_pool": candidate_pool_info,
                    "feasibility_diagnostics": {
                        "available_by_type": available_by_type,
                        "candidate_count": len(rows),
                        "maximum_candidate_confidence":
                            max((_num(row.get("confidence")) for row in rows), default=0.0),
                        "minimum_weighted_confidence":
                            1.0 - policy.max_weighted_uncertainty,
                    }}
        result = minimize(objective, feasible.x, method="SLSQP",
                          bounds=[(policy.min_asset_weight, policy.max_asset)] * n,
                          constraints=constraints,
                          options={"maxiter": 1000, "ftol": 1e-10})
    except Exception as exc:  # pragma: no cover - ambiente sem scipy
        return {"items": [], "status": "blocked", "can_publish": False,
                "blockers": [f"otimizador indisponível: {exc}"], "unresolved_dimensions": unresolved}

    if not result.success:
        return {"items": [], "status": "blocked", "can_publish": False,
                "blockers": [f"otimização inviável: {result.message}"],
                "unresolved_dimensions": sorted(set(unresolved)), "policy": asdict(policy)}

    # Elimine apenas ruído numérico. Cortar posições de 0,5% e renormalizar
    # podia elevar os pesos restantes e quebrar limites aprovados pelo solver.
    weights = np.where(result.x >= 1e-8, result.x, 0.0)
    weights = weights / weights.sum()
    items = [{**rows[i], "weight": float(weights[i])} for i in range(n) if weights[i] > 0]
    scenario_returns = {
        s: round(sum(item["weight"] * asset_scenario_return(item, s) for item in items), 6)
        for s in SCENARIOS
    }
    adverse_losses = sorted(max(-value, 0.0) for key, value in scenario_returns.items()
                            if key != "base")
    tail_count = max(1, int(np.ceil(len(adverse_losses) * .40))) if adverse_losses else 1
    scenario_cvar = (sum(adverse_losses[-tail_count:]) / tail_count if adverse_losses else 0.0)
    validation_block = any(item.get("publication_status") != "validated" for item in items)
    constraint_violations = portfolio_constraint_violations(items, bands, policy)
    blockers = []
    unresolved_critical = sorted(
        set(unresolved).intersection(policy.required_dimensions)
    )
    if unresolved_critical:
        blockers.append(
            "exposições obrigatórias sem cobertura suficiente: "
            + ", ".join(unresolved_critical)
        )
    if validation_block:
        blockers.append("há ativos/metodologia ainda classificados somente para diligência")
    if (float(correlation_penalty) > 0 and
            float(correlation_info.get("coverage") or 0.0) < policy.min_correlation_coverage):
        blockers.append(
            "cobertura de correlação abaixo do mínimo "
            f"{policy.min_correlation_coverage:.0%}"
        )
    blockers.extend(constraint_violations)
    return {
        "items": items, "status": "publishable" if not blockers else "diligence_only",
        "can_publish": not blockers, "blockers": blockers,
        "unresolved_dimensions": sorted(set(unresolved)),
        "unresolved_critical_dimensions": unresolved_critical,
        "dimension_coverage": dimension_info,
        "scenario_returns": scenario_returns,
        "scenario_cvar": round(scenario_cvar, 6),
        "weighted_uncertainty": round(sum(item["weight"] * (1 - _num(item.get("confidence")))
                                              for item in items), 6),
        "correlation_risk": (
            round(float(weights @ correlation_risk @ weights), 6)
            if correlation_risk is not None else None
        ),
        "correlation_info": correlation_info,
        "correlation_penalty": max(float(correlation_penalty), 0.0),
        "constraint_violations": list(constraint_violations),
        "trailing_yield_12m": round(sum(item["weight"] * (_num(item.get("dy_12m")) / (100 if _num(item.get("dy_12m")) > 1 else 1)) for item in items), 6),
        # Compatibilidade de leitura com modelos salvos antes da correção do rótulo.
        "expected_yield": round(sum(item["weight"] * (_num(item.get("dy_12m")) / (100 if _num(item.get("dy_12m")) > 1 else 1)) for item in items), 6),
        "effective_assets": round(1 / sum(item["weight"] ** 2 for item in items), 2),
        "macro_bands": bands, "band_adaptation": band_adaptation,
        "candidate_pool": candidate_pool_info,
        "turnover_penalty": policy.turnover_penalty,
        "policy": asdict(policy), "solver": str(result.message),
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
