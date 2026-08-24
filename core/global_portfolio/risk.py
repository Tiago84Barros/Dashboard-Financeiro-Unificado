"""Risco do patrimonio consolidado.

VaR e CVaR sao HISTORICOS, nao parametricos: com 60 observacoes mensais, supor
normalidade subestima a cauda justamente onde ela importa. O percentil empirico
nao supoe forma nenhuma.

Um ativo sem cotacao num mes NAO vira 0% (fillna(0.0) antes da media ponderada
puxaria a volatilidade para baixo, fabricando calmaria que nao existiu). Em vez
disso, o peso e renormalizado sobre os ativos que de fato tem retorno naquele
mes; se nenhum ativo tem retorno no mes, o mes cai fora da serie.

`contribuicao_marginal` decompoe essa mesma volatilidade por ativo (Euler nas
funcoes homogeneas de grau 1: a soma das contribuicoes fecha exatamente em
sigma_p). Ativo sem serie confiavel sai ausente do resultado, nunca com
contribuicao 0.0 — 0.0 diria "nao carrega risco", que e uma afirmacao sobre um
ativo que na verdade nao foi medido.

Coberto por tests/test_global_risk.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.global_portfolio.correlation import _covariancia_confiavel
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
    n_cauda: int


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

    # Quantos meses sustentam a cauda importa mais que o total: no piso de
    # MIN_OBS=18 observacoes, o percentil 5 cai entre o pior e o segundo pior
    # mes, `cauda` fica com UM elemento e o CVaR 95% passa a ser, por
    # definicao, o proprio pior mes -- verificado em 100% de 200 carteiras
    # sorteadas em 24/08/2026. Duas metricas exibidas lado a lado repousando
    # sobre a mesma unica observacao precisam dizer isso; `n_cauda` e o que a
    # UI usa para declarar. Ver `tests/test_global_var_cauda_declarada.py`.
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
        n_cauda=int(cauda.size),
    )


@dataclass(frozen=True)
class ContribuicaoRisco:
    """Quanto um ativo contribui para a volatilidade do portfolio.

    `contribuicao` esta na mesma unidade de `Risco.vol_mensal` (fracao
    mensal) e a soma de todas as contribuicoes fecha em sigma_p — e essa
    propriedade (Euler para funcoes homogeneas de grau 1) que torna a
    decomposicao legitima, nao decorativa.

    `razao` e contribuicao/peso: 1.0 significa que o ativo contribui na
    proporcao exata do seu peso; acima de 1.0, ele carrega mais risco do
    que a fatia sugere. Fica `None` quando o peso do ativo e zero — a
    razao nao tem leitura nesse caso (divisao por zero), mas a
    contribuicao em si e legitimamente 0.0 (peso zero contribui zero risco
    de fato, o que e diferente de "nao sabemos").
    """

    symbol: str
    contribuicao: float
    razao: float | None


def contribuicao_marginal(retornos: pd.DataFrame, pesos: dict) -> dict[str, ContribuicaoRisco]:
    """Contribuicao marginal de cada ativo a volatilidade do portfolio.

    MCR_i = w_i * (Sigma @ w)_i / sigma_p, com Sigma a covariancia e
    sigma_p = sqrt(w @ Sigma @ w). A soma dos MCR_i fecha exatamente em
    sigma_p (Euler nas funcoes homogeneas de grau 1) — e o teste central
    deste modulo.

    Reaproveita `_covariancia_confiavel` (mesmo piso de sobreposicao par a
    par que a matriz de correlacao usa): ativo sem serie confiavel — curta
    demais, sem sobreposicao suficiente com os demais, ou inteiramente NaN —
    e removido da covariancia e por isso sai AUSENTE do dict devolvido,
    nunca com contribuicao 0.0. Herda tambem a exigencia de pelo menos dois
    ativos: com um so, `_covariancia_confiavel` ja devolve vazio, entao o
    resultado aqui tambem e vazio.

    Pesos sao renormalizados sobre o subconjunto de ativos que sobrou da
    covariancia confiavel (mesmo criterio de `_cov_e_pesos` em
    correlation.py, para a nocao de "portfolio" ser a mesma nos dois
    modulos). Peso total nao positivo, ou frame vazio/com uma coluna so,
    devolve dict vazio.

    Saida ordenada por simbolo — deterministica independente da ordem das
    colunas de entrada ou da ordem de `pesos`.
    """
    if not isinstance(retornos, pd.DataFrame) or retornos.shape[1] < 2:
        return {}
    cov_df = _covariancia_confiavel(retornos)
    if cov_df.empty:
        return {}

    colunas = list(cov_df.columns)
    w_bruto = np.array([float(pesos.get(c, 0.0)) for c in colunas], dtype=float)
    total = w_bruto.sum()
    if total <= 0:
        return {}
    w = w_bruto / total

    cov = cov_df.to_numpy(dtype=float)
    variancia = float(w @ cov @ w)
    if variancia <= 0:
        return {}
    sigma_p = float(np.sqrt(variancia))
    contribuicoes = w * (cov @ w) / sigma_p

    por_simbolo = {
        str(simbolo): (float(c), float(peso))
        for simbolo, c, peso in zip(colunas, contribuicoes, w)
    }
    return {
        simbolo: ContribuicaoRisco(
            symbol=simbolo,
            contribuicao=c,
            razao=(c / peso) if peso > 0 else None,
        )
        for simbolo, (c, peso) in sorted(por_simbolo.items())
    }
