"""Restrições determinísticas e auditáveis para carteiras.

Este módulo não depende de Streamlit. Ele concentra invariantes que precisam
ser verdadeiros em qualquer tela ou backtest:

* pesos não negativos;
* soma dos pesos igual a 1;
* peso individual menor ou igual ao cap solicitado;
* configurações matematicamente inviáveis geram erro explícito.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np


class InfeasiblePortfolioConstraint(ValueError):
    """A restrição solicitada não admite uma carteira com soma igual a 1."""


def minimum_assets_for_cap(cap: float) -> int:
    """Número mínimo de ativos necessário para respeitar ``weight <= cap``."""
    cap = float(cap)
    if not 0 < cap <= 1:
        raise ValueError("cap deve estar no intervalo (0, 1].")
    return max(1, int(math.ceil((1.0 - 1e-12) / cap)))


def validate_cap_feasibility(n_assets: int, cap: float) -> None:
    """Falha de forma explícita quando ``n_assets * cap < 1``."""
    if n_assets <= 0:
        raise InfeasiblePortfolioConstraint("A carteira precisa de ao menos um ativo.")
    required = minimum_assets_for_cap(cap)
    if n_assets < required:
        raise InfeasiblePortfolioConstraint(
            f"Cap de {cap:.1%} exige ao menos {required} ativos; "
            f"somente {n_assets} estão disponíveis."
        )


def project_capped_simplex(
    weights: Mapping[str, float],
    cap: float,
    *,
    tolerance: float = 1e-12,
) -> dict[str, float]:
    """Projeta pesos no simplex ``sum(w)=1, 0<=w<=cap``.

    A projeção euclidiana é calculada por bisseção de ``lambda`` em
    ``clip(v-lambda, 0, cap)``. Ao contrário de ``clip + normalize``, a
    renormalização nunca reintroduz pesos acima do cap.
    """
    keys = list(weights)
    if not keys:
        return {}
    validate_cap_feasibility(len(keys), cap)

    values = np.asarray(
        [max(float(weights.get(key, 0.0) or 0.0), 0.0) for key in keys],
        dtype=float,
    )
    values[~np.isfinite(values)] = 0.0
    if float(values.sum()) <= tolerance:
        values = np.full(len(keys), 1.0 / len(keys), dtype=float)
    else:
        values = values / values.sum()

    lo = float(values.min() - cap)
    hi = float(values.max())
    for _ in range(200):
        mid = (lo + hi) / 2.0
        projected = np.clip(values - mid, 0.0, cap)
        if projected.sum() > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo <= tolerance:
            break

    result = np.clip(values - (lo + hi) / 2.0, 0.0, cap)
    residual = 1.0 - float(result.sum())
    if abs(residual) > tolerance:
        if residual > 0:
            order = np.argsort(-(cap - result))
            for idx in order:
                add = min(residual, cap - result[idx])
                result[idx] += add
                residual -= add
                if residual <= tolerance:
                    break
        else:
            order = np.argsort(-result)
            for idx in order:
                remove = min(-residual, result[idx])
                result[idx] -= remove
                residual += remove
                if residual >= -tolerance:
                    break

    if abs(float(result.sum()) - 1.0) > 1e-9:
        raise RuntimeError("Falha numérica ao projetar pesos no simplex.")
    if float(result.max()) > float(cap) + 1e-9:
        raise RuntimeError("Falha numérica: projeção excedeu o cap.")
    return {key: float(result[idx]) for idx, key in enumerate(keys)}
