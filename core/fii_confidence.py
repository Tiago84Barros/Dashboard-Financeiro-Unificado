"""Calibração empírica e conservadora da confiança dos dados de FIIs.

O score de confiança metodológico mede cobertura e qualidade da evidência. Este
módulo acrescenta uma segunda camada: a taxa de acerto observada em revisões
humanas. Sem amostra revisada suficiente, usa um prior conservador e nunca
transforma ausência de validação em confiança elevada.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True)
class Calibration:
    parser_name: str
    parser_version: str
    metric_name: str
    reviewed: int
    accepted: int
    corrected: int
    rejected: int
    posterior_mean: float
    lower_bound: float
    upper_bound: float


def beta_posterior(
    accepted: int,
    corrected: int = 0,
    rejected: int = 0,
    *,
    prior_alpha: float = 4.0,
    prior_beta: float = 2.0,
    correction_credit: float = 0.35,
) -> dict[str, float | int]:
    """Posterior beta-binomial; correções recebem crédito parcial.

    ``lower_bound`` usa a aproximação normal apenas para evitar uma dependência
    obrigatória de SciPy no app. Quando SciPy estiver disponível, o quantil beta
    exato é utilizado.
    """
    accepted = max(int(accepted), 0)
    corrected = max(int(corrected), 0)
    rejected = max(int(rejected), 0)
    reviewed = accepted + corrected + rejected
    successes = accepted + correction_credit * corrected
    failures = rejected + (1.0 - correction_credit) * corrected
    alpha = float(prior_alpha) + successes
    beta = float(prior_beta) + failures
    mean = alpha / (alpha + beta)
    try:
        from scipy.stats import beta as beta_distribution

        lower = float(beta_distribution.ppf(.05, alpha, beta))
        upper = float(beta_distribution.ppf(.95, alpha, beta))
    except Exception:  # pragma: no cover - fallback para ambiente mínimo
        variance = alpha * beta / (((alpha + beta) ** 2) * (alpha + beta + 1))
        margin = 1.6448536269514722 * math.sqrt(max(variance, 0.0))
        lower, upper = max(0.0, mean - margin), min(1.0, mean + margin)
    return {
        "reviewed": reviewed,
        "posterior_mean": mean,
        "lower_bound": lower,
        "upper_bound": upper,
    }


def calibration_factor(
    base_confidence: float,
    *,
    reviewed: int,
    posterior_mean: float,
    lower_bound: float,
    full_weight_after: int = 30,
) -> float:
    """Combina confiança estrutural e acurácia observada sem falsa precisão."""
    base = min(max(float(base_confidence), 0.0), 1.0)
    sample_weight = min(max(int(reviewed), 0) / max(int(full_weight_after), 1), 1.0)
    empirical = .65 * float(lower_bound) + .35 * float(posterior_mean)
    # Antes de haver revisões, a calibração é neutra; depois passa a dominar de
    # forma gradual. Isso evita premiar ou punir um parser por uma amostra ínfima.
    return min(max(base * ((1.0 - sample_weight) + sample_weight * empirical), 0.0), 1.0)


def aggregate_calibrations(rows: Iterable[dict]) -> dict[str, float]:
    """Retorna fator por métrica ponderado pelo número de revisões."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("metric_name") or "unknown"), []).append(dict(row))
    output: dict[str, float] = {}
    for metric, items in grouped.items():
        total = sum(max(int(item.get("reviewed") or 0), 1) for item in items)
        output[metric] = sum(
            float(item.get("lower_bound") or 0.0) * max(int(item.get("reviewed") or 0), 1)
            for item in items
        ) / total
    return output
