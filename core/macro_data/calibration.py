"""Calibração setorial experimental com treino móvel e validação temporal.

Sinal em [-1,1] conhecido no corte t explica retorno em fração no mês t+1.
Coeficiente padronizado é encolhido para a premissa; não é beta de mercado.
Nenhum resultado reconstruído pode ser promovido a parâmetro calibrado.
"""
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CalibrationResult:
    status: str
    sensitivity: float
    prior: float
    observations: int
    oos_observations: int
    oos_r2: float | None
    sign_stability: float | None
    limitations: tuple[str, ...]

    def to_payload(self):
        return asdict(self)


def calibrate_sector_factor(panel: pd.DataFrame, *, prior: float,
                            knowledge_mode: str = "strict", window: int = 36,
                            min_oos: int = 24) -> CalibrationResult:
    """Uma linha por mês: cutoff, known_at, return_end, signal, forward_return.

    Folds usam apenas retornos encerrados até o corte do teste. O benchmark
    preditivo é a média do treino. Exige ganho OOS > 0 e sinal estável >= 80%.
    Mesmo aprovado, sai como candidato para revisão, nunca altera o banco.
    """
    if not np.isfinite(prior) or not -1 <= prior <= 1:
        raise ValueError("premissa inválida")
    if window < 12 or min_oos < 12:
        raise ValueError("amostra de calibração insuficiente")
    def fallback(reason, n=0, oos=0, r2=None, stability=None):
        return CalibrationResult("initial_prior", prior, prior, n, oos, r2, stability, (reason,))
    if knowledge_mode != "strict":
        return fallback("reconstrução ex post não autoriza calibração")
    if panel.empty:
        return fallback("sem histórico point-in-time para calibração")
    work = panel.copy()
    for column in ("cutoff", "known_at", "return_end"):
        work[column] = pd.to_datetime(work[column], utc=True, errors="coerce")
    for column in ("signal", "forward_return"):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.replace([np.inf, -np.inf], np.nan).dropna()
    work = work[(work.known_at <= work.cutoff) & (work.return_end > work.cutoff)]
    work = work.sort_values("cutoff")
    if work.cutoff.dt.strftime("%Y-%m").duplicated().any():
        return fallback("períodos duplicados na calibração")
    n = len(work)
    predictions, means, actuals, slopes = [], [], [], []
    for row in work.itertuples():
        train = work[(work.return_end <= row.cutoff) & (work.cutoff < row.cutoff)].tail(window)
        if len(train) < window or train.signal.nunique() < 12:
            continue
        x, y = train.signal.to_numpy(), train.forward_return.to_numpy()
        if x.std() < 1e-10 or y.std() < 1e-10:
            continue
        slope = float(np.cov(x, y, ddof=0)[0, 1] / x.var())
        predictions.append(float(y.mean() + slope * (row.signal - x.mean())))
        means.append(float(y.mean()))
        actuals.append(float(row.forward_return))
        slopes.append(slope * x.std() / y.std())
    if len(slopes) < min_oos:
        return fallback("histórico insuficiente: mínimo 36 meses de treino e 24 de validação", n, len(slopes))
    errors = np.asarray(actuals) - predictions
    null_errors = np.asarray(actuals) - means
    denom = float(np.sum(null_errors**2))
    r2 = 1 - float(np.sum(errors**2)) / denom if denom > 0 else None
    stability = float(max(np.mean(np.asarray(slopes) > 0), np.mean(np.asarray(slopes) < 0)))
    if r2 is None or r2 <= 0 or stability < .8:
        return fallback("sem vantagem preditiva estável fora da amostra", n, len(slopes), r2, stability)
    candidate = float(np.clip(.5 * prior + .5 * np.median(slopes), -1, 1))
    return CalibrationResult("candidate_for_review", candidate, prior, n, len(slopes), r2,
                             stability, ("validação preditiva; não prova retorno líquido após custos",
                                         "exige revisão humana e validação da carteira antes de ativação"))
