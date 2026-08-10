"""Risco do patrimonio consolidado.

VaR e CVaR sao HISTORICOS, nao parametricos: com 60 observacoes mensais, supor
normalidade subestima a cauda justamente onde ela importa. O percentil empirico
nao supoe forma nenhuma.

Um ativo sem cotacao num mes NAO vira 0% (fillna(0.0) antes da media ponderada
puxaria a volatilidade para baixo, fabricando calmaria que nao existiu). Em vez
disso, o peso e renormalizado sobre os ativos que de fato tem retorno naquele
mes; se nenhum ativo tem retorno no mes, o mes cai fora da serie.

Coberto por tests/test_global_risk.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.global_portfolio.returns import MIN_OBS

PERCENTIL_VAR = 5


@dataclass(frozen=True)
class Risco:
    """Risco do portfolio sintetico, em base mensal e anualizada."""

    vol_mensal: float
    vol_anual: float
    var_95: float
    cvar_95: float
    drawdown_max: float
    n_obs: int


def retorno_do_portfolio(retornos: pd.DataFrame, pesos: dict) -> pd.Series | None:
    """Serie de retornos do portfolio sintetico, ponderada e renormalizada.

    Para cada mes, o peso e recalculado apenas sobre os ativos com retorno
    disponivel naquele mes (um ativo ausente e excluido do mes, nao contado
    como zero). Um mes em que nenhum ativo tem retorno nao entra na serie.
    """
    if not isinstance(retornos, pd.DataFrame) or retornos.empty:
        return None
    w = pd.Series(
        [float(pesos.get(c, 0.0)) for c in retornos.columns], index=retornos.columns)
    if w.sum() <= 0:
        return None

    disponivel = retornos.notna()
    peso_por_mes = disponivel.mul(w, axis=1).sum(axis=1)
    contribuicao = retornos.fillna(0.0).mul(w, axis=1).sum(axis=1)

    serie = (contribuicao / peso_por_mes).where(peso_por_mes > 0)
    return serie.dropna()


def _drawdown_maximo(serie: pd.Series) -> float:
    """Maior queda percentual do pico ate o vale, como numero positivo."""
    acumulado = (1.0 + serie.fillna(0.0)).cumprod()
    pico = acumulado.cummax()
    return float((1.0 - acumulado / pico).max())


def metricas_de_risco(retornos: pd.DataFrame, pesos: dict) -> Risco | None:
    """Volatilidade, VaR/CVaR historicos e drawdown do patrimonio."""
    serie = retorno_do_portfolio(retornos, pesos)
    if serie is None or len(serie) < MIN_OBS:
        return None

    vol_mensal = float(serie.std(ddof=1))
    valores = serie.to_numpy(dtype=float)
    corte = float(np.percentile(valores, PERCENTIL_VAR))
    cauda = valores[valores <= corte]

    return Risco(
        vol_mensal=vol_mensal,
        vol_anual=vol_mensal * float(np.sqrt(12)),
        var_95=float(-corte),
        cvar_95=float(-cauda.mean()) if cauda.size else float(-corte),
        drawdown_max=_drawdown_maximo(serie),
        n_obs=len(serie),
    )
