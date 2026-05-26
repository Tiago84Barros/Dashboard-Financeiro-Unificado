"""
core/correlations.py — correlações com decay temporal (EWMA).

Implementa a recomendação M2 (parcial — MVP) do parecer da banca
examinadora (2026-05-23): substituir Pearson estático por correlação
ponderada exponencialmente (EWMA), proxy simples de DCC-GARCH que
captura time-varying correlations sem custo computacional alto.

Motivação acadêmica:
  Pearson assume estacionariedade — todas as observações têm peso 1.
  Mas em mercados financeiros correlações sobem em estresse
  (Sandoval & Franca, 2012, "Correlation of financial markets in
  times of crisis"). EWMA com half-life de 60 dias dá peso 2x maior
  às observações dos últimos 60 dias vs 120-180 dias atrás,
  capturando regime atual.

MVP (esta versão):
  • ewma_correlation_matrix(returns, halflife) → matriz de correlação
  • ewma_volatility(returns, halflife) → vetor de vol anualizada
  • correlation_regime_score(returns_atual, returns_baseline) →
    score 0-1 indicando o quanto a correlação atual diverge da histórica

Pendente (versão completa, ~30h adicionais):
  • DCC-GARCH propriamente dito (Engle 2002) — requer otimização ML
    iterativa, mais complexa
  • Copulas (M2c — Embrechts, McNeil & Straumann 2002) para tail dependence
  • Integração com _build_corr_data() da view investimentos

Referências:
  RiskMetrics (1996) — EWMA com λ=0.94 (~75 dias half-life)
  Engle (2002) — Dynamic Conditional Correlation
"""
from __future__ import annotations

from math import log
from typing import Sequence

import numpy as np


# ──────────────────────────────────────────────────────────────────────────
# Parâmetros default — calibrados em literatura
# ──────────────────────────────────────────────────────────────────────────

HALFLIFE_DEFAULT = 60     # dias úteis ≈ 3 meses corridos
EWMA_LAMBDA_RISKMETRICS = 0.94  # padrão RiskMetrics (~75d half-life)


def _halflife_to_lambda(halflife: float) -> float:
    """Converte half-life em dias para lambda EWMA."""
    if halflife <= 0:
        return EWMA_LAMBDA_RISKMETRICS
    return 0.5 ** (1.0 / halflife)


def _ewma_weights(n: int, lam: float) -> np.ndarray:
    """Pesos exponencialmente decrescentes, mais recente = peso maior."""
    w = lam ** np.arange(n - 1, -1, -1)
    return w / w.sum()


def ewma_covariance_matrix(
    returns: np.ndarray,
    halflife: float = HALFLIFE_DEFAULT,
) -> np.ndarray:
    """Calcula matriz de covariância com pesos EWMA.

    Args:
      returns: (T, N) matriz de retornos. T linhas = observações temporais
               (mais recente por último). N colunas = ativos.
      halflife: meia-vida em períodos (default 60 dias úteis).

    Returns:
      (N, N) matriz de covariância. Diagonais = variâncias EWMA.
    """
    if returns.ndim != 2 or returns.shape[0] < 2:
        raise ValueError("returns deve ser (T, N) com T >= 2")
    T, N = returns.shape
    lam = _halflife_to_lambda(halflife)
    w = _ewma_weights(T, lam)

    # Média ponderada
    mean = (returns * w[:, None]).sum(axis=0)
    centered = returns - mean

    # Covariância ponderada: cov_ij = sum_t w_t * c_ti * c_tj
    cov = (centered.T * w) @ centered
    # Correção pequeno-amostral (Bessel-style aproximado)
    n_eff = 1.0 / (w ** 2).sum()
    if n_eff > 1:
        cov = cov * n_eff / (n_eff - 1)
    return cov


def ewma_correlation_matrix(
    returns: np.ndarray,
    halflife: float = HALFLIFE_DEFAULT,
) -> np.ndarray:
    """Matriz de correlação a partir de EWMA covariance."""
    cov = ewma_covariance_matrix(returns, halflife)
    std = np.sqrt(np.diag(cov))
    std_safe = np.where(std > 1e-12, std, 1e-12)
    corr = cov / (std_safe[:, None] * std_safe[None, :])
    # Garante diagonais exatamente 1.0 e clip [-1, 1]
    np.fill_diagonal(corr, 1.0)
    return np.clip(corr, -1.0, 1.0)


def ewma_volatility(
    returns: np.ndarray,
    halflife: float = HALFLIFE_DEFAULT,
    periods_per_year: int = 252,
) -> np.ndarray:
    """Vetor de volatilidades anualizadas EWMA (N ativos)."""
    cov = ewma_covariance_matrix(returns, halflife)
    return np.sqrt(np.diag(cov)) * np.sqrt(periods_per_year)


def correlation_regime_score(
    returns_atual: np.ndarray,
    returns_baseline: np.ndarray,
    halflife: float = HALFLIFE_DEFAULT,
) -> float:
    """Quantifica divergência entre correlação atual e baseline histórico.

    Returns:
      Score em [0, 1]:
        0.0 = correlações praticamente iguais (regime estável)
        0.5 = mudança moderada
        1.0 = regime completamente diferente (ex: crise vs bull market)

    Métrica: norma de Frobenius da diferença das matrizes de correlação,
    normalizada pelo número de pares.
    """
    if returns_atual.shape[1] != returns_baseline.shape[1]:
        raise ValueError("Mesmo número de ativos em ambos os períodos")
    N = returns_atual.shape[1]
    if N < 2:
        return 0.0
    corr_atual    = ewma_correlation_matrix(returns_atual, halflife)
    corr_baseline = ewma_correlation_matrix(returns_baseline, halflife)
    diff = corr_atual - corr_baseline
    # Frobenius / N (cada elemento contribui no máximo 2.0 para o diff)
    score = np.linalg.norm(diff, "fro") / (2.0 * N)
    return float(np.clip(score, 0.0, 1.0))


def average_pairwise_correlation(
    returns: np.ndarray,
    halflife: float = HALFLIFE_DEFAULT,
) -> float:
    """Correlação média entre todos os pares (off-diagonal).

    Útil para sinalizar "mercado em pânico" — quando essa média cresce
    bruscamente (todos andam juntos), reduz efeito de diversificação.
    """
    corr = ewma_correlation_matrix(returns, halflife)
    N = corr.shape[0]
    if N < 2:
        return 0.0
    # Soma off-diagonal / número de pares
    return float((corr.sum() - N) / (N * (N - 1)))
