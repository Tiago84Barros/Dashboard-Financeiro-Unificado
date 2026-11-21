"""Alocação parcial, sem normalizar posições acima dos limites de proteção.

Estágios lexicográficos: maximizar capital alocável sob limites rígidos;
depois, quando há metas (``target_bands``), minimizar a soma dos desvios fora
das faixas mantendo esse capital; por fim maximizar a qualidade observada,
mantendo capital e desvio já obtidos. Meta nunca veta: o que não cabe nela
vira desvio medido, não capital zerado.
O saldo é capital não alocado (sem ativo, rentabilidade ou liquidez presumidos).
Bandas mínimas e aprovação estatística não são vetos neste modo de diligência.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


def allocate_partial(rows, caps, *, upper_constraints=(), target_bands=(),
                     max_assets=20, min_weight=0.0, reasons=()):
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
    bands = []
    for mask, lower, upper in target_bands:
        mask = np.asarray(mask, dtype=float)
        if (mask.shape != (n,) or not np.isfinite(mask).all()
                or not 0 <= lower <= upper <= 1):
            raise ValueError("Meta por faixa inválida")
        bands.append((mask, float(lower), float(upper)))
    k = len(bands)
    # Variáveis: pesos (n), seleção binária (n), desvio abaixo e acima de cada
    # faixa (2k). O desvio cabe em [0, 1] porque pesos e faixas cabem.
    width = 2*n + 2*k
    upper_bounds = np.r_[caps, np.ones(n), np.ones(2*k)]
    matrix, lo, hi = [], [], []

    def vector(weights=None, picks=None, deviations=None):
        values = np.zeros(width)
        if weights is not None:
            values[:n] = weights
        if picks is not None:
            values[n:2*n] = picks
        if deviations is not None:
            values[2*n:] = deviations
        return values

    def add(values, lower, upper):
        matrix.append(values)
        lo.append(lower)
        hi.append(upper)

    add(vector(weights=np.ones(n)), 0, 1)
    for exposure, limit in upper_constraints:
        exposure = np.asarray(exposure, dtype=float)
        if (exposure.shape != (n,) or not np.isfinite(exposure).all()
                or not np.isfinite(limit) or limit < 0):
            raise ValueError("Exposição inválida")
        add(vector(weights=exposure), -np.inf, limit)
    add(vector(picks=np.ones(n)), 0, int(max_assets))
    for i in range(n):
        row = np.zeros(width)
        row[i], row[n+i] = 1, -caps[i]
        add(row, -np.inf, 0)
        row = np.zeros(width)
        row[i], row[n+i] = 1, -min_weight
        add(row, 0, np.inf)
    for j, (mask, lower, upper) in enumerate(bands):
        below, above = np.zeros(2*k), np.zeros(2*k)
        below[2*j], above[2*j+1] = 1, -1
        add(vector(weights=mask, deviations=below), lower, np.inf)
        add(vector(weights=mask, deviations=above), -np.inf, upper)

    def solve(objective):
        return milp(objective, integrality=np.r_[np.zeros(n), np.ones(n), np.zeros(2*k)],
                    bounds=Bounds(np.zeros(width), upper_bounds),
                    constraints=LinearConstraint(np.array(matrix), lo, hi),
                    options={"time_limit": 10})

    def feasible(solution):
        if solution.x is None:
            return False
        values = np.asarray(solution.x, dtype=float)
        if values.shape != (width,) or not np.isfinite(values).all():
            return False
        residual = np.asarray(matrix) @ values
        picks = values[n:2*n]
        return bool((values >= -1e-7).all()
                    and (values <= upper_bounds+1e-7).all()
                    and (np.abs(picks-np.round(picks)) <= 1e-7).all()
                    and (residual >= np.array(lo)-1e-7).all()
                    and (residual <= np.array(hi)+1e-7).all())

    first = solve(vector(weights=-np.ones(n)))
    if not feasible(first):
        return {**empty, "solver_status": "not_proven",
                "reasons": [*reasons, "Não foi possível comprovar a alocação nesta execução."]}
    total = float(first.x[:n].sum())
    add(vector(weights=np.ones(n)), max(0, total-1e-8), total+1e-8)
    incumbent, proven = first, bool(first.success)
    if k:
        # Sem um teto de desvio, o estágio da qualidade desfaria as metas.
        targets = solve(vector(deviations=np.ones(2*k)))
        if feasible(targets):
            deviation = float(targets.x[2*n:].sum())
            add(vector(deviations=np.ones(2*k)), 0, deviation+1e-8)
            incumbent, proven = targets, proven and bool(targets.success)
        else:
            proven = False
    second = solve(vector(weights=-quality, picks=np.full(n, 1e-8)))
    second_valid = feasible(second)
    if second_valid:
        incumbent = second
    proven = proven and bool(second.success) and second_valid
    weights = incumbent.x[:n]
    weights = np.clip(weights, 0, caps)
    # Verificação independente das restrições; nunca renormalizar para 100%.
    if (weights.sum() > 1+1e-6 or np.count_nonzero(weights > 1e-7) > max_assets
            or any(np.dot(exposure, weights) > limit+1e-6
                   for exposure, limit in upper_constraints)):
        raise ValueError("Alocação viola limite de proteção")
    items = [{**row, "weight": float(weight)} for row, weight in zip(rows, weights)
             if weight > 1e-7]
    total = sum(row["weight"] for row in items)
    notes = list(reasons)
    if not proven:
        notes.append("Composição factível encontrada; o limite de cálculo não permitiu "
                     "comprovar que é a melhor solução.")
    return {**empty, "items": sorted(items, key=lambda row: (-row["weight"], row["ticker"])),
            "allocated_weight": total, "unallocated_weight": max(0, 1-total),
            "reasons": notes, "solver_status": "optimal" if proven else "feasible",
            **({"target_deviation": float(sum(
                max(lower - float(mask @ weights), 0) + max(float(mask @ weights) - upper, 0)
                for mask, lower, upper in bands))} if k else {})}
