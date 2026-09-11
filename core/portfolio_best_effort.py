"""Alocação parcial, sem normalizar posições acima dos limites de proteção.

Dois estágios lexicográficos: maximizar capital alocável sob limites rígidos;
depois maximizar qualidade observada, mantendo o capital obtido no estágio 1.
O saldo é capital não alocado (sem ativo, rentabilidade ou liquidez presumidos).
Bandas mínimas e aprovação estatística não são vetos neste modo de diligência.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


def allocate_partial(rows, caps, *, upper_constraints=(), max_assets=20,
                     min_weight=0.0, reasons=()):
    rows = [dict(row) for row in rows]
    n = len(rows)
    empty = {"items": [], "unallocated_weight": 1.0, "allocated_weight": 0.0,
             "can_publish": False, "status": "allocation_review",
             "reasons": list(reasons), "solver_status": "no_candidates"}
    if not n:
        return empty
    caps = np.asarray(caps, dtype=float)
    quality = np.asarray([row["quality"] for row in rows], dtype=float)
    if (caps.shape != (n,) or not np.isfinite(caps).all()
            or not np.isfinite(quality).all() or (caps < 0).any()
            or (caps > 1).any() or max_assets < 1 or not 0 <= min_weight <= 1):
        raise ValueError("Parâmetros de alocação inválidos")
    matrix, lo, hi = [], [], []

    def add(values, lower, upper):
        matrix.append(values)
        lo.append(lower)
        hi.append(upper)

    add(np.r_[np.ones(n), np.zeros(n)], 0, 1)
    for exposure, limit in upper_constraints:
        exposure = np.asarray(exposure, dtype=float)
        if (exposure.shape != (n,) or not np.isfinite(exposure).all()
                or not np.isfinite(limit) or limit < 0):
            raise ValueError("Exposição inválida")
        add(np.r_[exposure, np.zeros(n)], -np.inf, limit)
    add(np.r_[np.zeros(n), np.ones(n)], 0, int(max_assets))
    for i in range(n):
        row = np.zeros(2*n)
        row[i], row[n+i] = 1, -caps[i]
        add(row, -np.inf, 0)
        row = np.zeros(2*n)
        row[i], row[n+i] = 1, -min_weight
        add(row, 0, np.inf)

    def solve(objective):
        return milp(objective, integrality=np.r_[np.zeros(n), np.ones(n)],
                    bounds=Bounds(np.zeros(2*n), np.r_[caps, np.ones(n)]),
                    constraints=LinearConstraint(np.array(matrix), lo, hi),
                    options={"time_limit": 10})

    def feasible(solution):
        if solution.x is None:
            return False
        values = np.asarray(solution.x, dtype=float)
        if values.shape != (2*n,) or not np.isfinite(values).all():
            return False
        residual = np.asarray(matrix) @ values
        return bool((values >= -1e-7).all()
                    and (values <= np.r_[caps, np.ones(n)]+1e-7).all()
                    and (np.abs(values[n:]-np.round(values[n:])) <= 1e-7).all()
                    and (residual >= np.array(lo)-1e-7).all()
                    and (residual <= np.array(hi)+1e-7).all())

    first = solve(np.r_[-np.ones(n), np.zeros(n)])
    if not feasible(first):
        return {**empty, "solver_status": "not_proven",
                "reasons": [*reasons, "Não foi possível comprovar a alocação nesta execução."]}
    total = float(first.x[:n].sum())
    add(np.r_[np.ones(n), np.zeros(n)], max(0, total-1e-8), total+1e-8)
    second = solve(np.r_[-quality, np.full(n, 1e-8)])
    second_valid = feasible(second)
    weights = (second.x if second_valid else first.x)[:n]
    weights = np.clip(weights, 0, caps)
    # Verificação independente das restrições; nunca renormalizar para 100%.
    if (weights.sum() > 1+1e-6 or np.count_nonzero(weights > 1e-7) > max_assets
            or any(np.dot(exposure, weights) > limit+1e-6
                   for exposure, limit in upper_constraints)):
        raise ValueError("Alocação viola limite de proteção")
    items = [{**row, "weight": float(weight)} for row, weight in zip(rows, weights)
             if weight > 1e-7]
    total = sum(row["weight"] for row in items)
    proven = first.success and second.success and second_valid
    notes = list(reasons)
    if not proven:
        notes.append("Composição factível encontrada; o limite de cálculo não permitiu "
                     "comprovar que é a melhor solução.")
    return {**empty, "items": sorted(items, key=lambda row: (-row["weight"], row["ticker"])),
            "allocated_weight": total, "unallocated_weight": max(0, 1-total),
            "reasons": notes, "solver_status": "optimal" if proven else "feasible"}
