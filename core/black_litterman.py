"""
core/black_litterman.py — Black-Litterman para incorporar views do usuário.

Implementa a recomendação M3 (MVP) do parecer da banca examinadora
(2026-05-23): permitir que o usuário injete views explícitas
("acho que PETR3 vai render 20% acima da média dos pares") combinadas
com retornos implícitos de equilíbrio de mercado via formalismo
Bayesiano clássico de Black & Litterman (1991, 1992).

Vantagem sobre Markowitz puro:
  Markowitz mean-variance usa retornos esperados PONTUAIS — pequenas
  perturbações nesses inputs produzem pesos drasticamente diferentes
  (notório problema de estabilidade). Black-Litterman parte de pesos
  de equilíbrio (estáveis por construção) + views do usuário com
  confiança quantificada → retornos posteriores Bayesianos mais
  estáveis numericamente.

Fórmula central (Litterman 1992):

  π    = δ Σ w_mkt                      (retornos implícitos do equilíbrio)
  E[R] = [(τΣ)⁻¹ + Pᵀ Ω⁻¹ P]⁻¹ × [(τΣ)⁻¹ π + Pᵀ Ω⁻¹ Q]
  Σ_BL = [(τΣ)⁻¹ + Pᵀ Ω⁻¹ P]⁻¹  +  Σ    (covariância posterior)

Onde:
  π:   prior de retorno esperado (equilíbrio do CAPM)
  Σ:   covariância amostral dos retornos
  τ:   incerteza na precisão do prior (0.025–0.05 típico)
  P:   matriz de "pick" das views (K × N)
  Q:   vetor de magnitude das views (K × 1)
  Ω:   matriz de confiança nas views (diagonal K × K)

MVP (esta versão):
  • BLView dataclass (absolute e relative)
  • posterior_returns(prior, sigma, views, tau)
  • posterior_covariance(sigma, views, tau)
  • bl_combined_optimization(...) — devolve retorno + covar prontos para
    Markowitz (compatível com core/markowitz.py)

Pendente (versão completa, ~40h adicionais):
  • Reverse optimization de pesos de mercado IBOV → π implícito
  • Calibração automática de τ via maximum likelihood
  • Múltiplos níveis de confiança por view + agrupamento setorial
  • UI dedicada na Empresas B3 para inserção de views

Referências:
  Black & Litterman (1991, 1992) — Goldman Sachs Quantitative Strategy
  Idzorek (2005) — "A Step-by-Step Guide to the Black-Litterman Model"
  He & Litterman (1999) — "The Intuition Behind Black-Litterman Model"
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ──────────────────────────────────────────────────────────────────────────
# Estruturas
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class BLView:
    """Representa uma view do investidor sobre retornos relativos ou absolutos.

    Tipos:
      'absolute' — "PETR3 vai render 18% no ano"
                   tickers = ['PETR3']; weights = [1.0]; expected_return = 0.18
      'relative' — "PETR3 vai render 5pp acima de VALE3"
                   tickers = ['PETR3', 'VALE3']; weights = [1.0, -1.0];
                   expected_return = 0.05

    Atributos:
      confidence: 0..1, quão certo o usuário está. Alta confiança (>0.8)
                  força os retornos posteriores a respeitarem a view;
                  baixa (<0.3) deixa o prior dominante.
    """
    view_type:        str         # 'absolute' | 'relative'
    tickers:          list[str]
    weights:          list[float]
    expected_return:  float
    confidence:       float = 0.5

    def __post_init__(self):
        if self.view_type not in ("absolute", "relative"):
            raise ValueError(f"view_type invalido: {self.view_type}")
        if len(self.tickers) != len(self.weights):
            raise ValueError("tickers e weights devem ter mesmo comprimento")
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError("confidence deve estar em (0, 1]")


# ──────────────────────────────────────────────────────────────────────────
# Matrizes de views
# ──────────────────────────────────────────────────────────────────────────

def _build_views_matrices(
    views:       list[BLView],
    all_tickers: list[str],
    sigma:       np.ndarray,
    tau:         float = 0.025,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Constrói P (K×N), Q (K), Omega (K×K) a partir das views.

    Omega usa especificação de Idzorek (2005): variância da view igual
    a (1/confidence - 1) × τ × pᵢᵀ Σ pᵢ, normalizando a confiança em
    "fração da variância do equilíbrio".
    """
    K = len(views)
    N = len(all_tickers)
    if K == 0:
        return np.zeros((0, N)), np.zeros(0), np.zeros((0, 0))

    idx = {t: i for i, t in enumerate(all_tickers)}
    P = np.zeros((K, N))
    Q = np.zeros(K)
    omega_diag = np.zeros(K)

    for k, view in enumerate(views):
        for tk, w in zip(view.tickers, view.weights):
            if tk not in idx:
                raise ValueError(f"Ticker {tk} da view {k} não está em all_tickers")
            P[k, idx[tk]] = w
        Q[k] = view.expected_return
        # Idzorek: var_view = (1/conf - 1) * tau * pᵀ Σ p
        # (confidence alta → var pequena → view domina)
        pSp = float(P[k] @ sigma @ P[k])
        if pSp <= 0:
            pSp = 1e-8
        omega_diag[k] = max((1.0 / view.confidence - 1.0) * tau * pSp, 1e-10)

    Omega = np.diag(omega_diag)
    return P, Q, Omega


# ──────────────────────────────────────────────────────────────────────────
# Black-Litterman core
# ──────────────────────────────────────────────────────────────────────────

def posterior_returns(
    prior_returns: np.ndarray,
    sigma:         np.ndarray,
    views:         list[BLView],
    all_tickers:   list[str],
    tau:           float = 0.025,
) -> np.ndarray:
    """Calcula E[R] posteriori combinando prior e views (Litterman 1992).

    Args:
      prior_returns: π — vetor (N,) de retornos esperados a priori
      sigma:         Σ — matriz (N, N) de covariância amostral
      views:         lista de BLView
      all_tickers:   ordem canônica dos N ativos
      tau:           incerteza do prior (0.025-0.05 típico)

    Returns:
      Vetor (N,) com retornos esperados posteriores Bayesianos.
    """
    N = len(prior_returns)
    if sigma.shape != (N, N):
        raise ValueError(f"sigma deve ser ({N}, {N})")

    if not views:
        return prior_returns.copy()

    P, Q, Omega = _build_views_matrices(views, all_tickers, sigma, tau)
    tau_sigma_inv = np.linalg.pinv(tau * sigma)

    # E[R] = [(τΣ)⁻¹ + Pᵀ Ω⁻¹ P]⁻¹ × [(τΣ)⁻¹ π + Pᵀ Ω⁻¹ Q]
    omega_inv = np.linalg.pinv(Omega)
    A = tau_sigma_inv + P.T @ omega_inv @ P
    b = tau_sigma_inv @ prior_returns + P.T @ omega_inv @ Q
    return np.linalg.pinv(A) @ b


def posterior_covariance(
    sigma:        np.ndarray,
    views:        list[BLView],
    all_tickers:  list[str],
    tau:          float = 0.025,
) -> np.ndarray:
    """Calcula covariância posterior (Σ_BL = M⁻¹ + Σ) com M de Litterman."""
    if not views:
        return sigma.copy()

    P, _, Omega = _build_views_matrices(views, all_tickers, sigma, tau)
    tau_sigma_inv = np.linalg.pinv(tau * sigma)
    omega_inv     = np.linalg.pinv(Omega)
    M = tau_sigma_inv + P.T @ omega_inv @ P
    return np.linalg.pinv(M) + sigma


# ──────────────────────────────────────────────────────────────────────────
# Helper para integração com core/markowitz.py
# ──────────────────────────────────────────────────────────────────────────

def bl_combined_optimization(
    tickers:        list[str],
    prior_returns:  np.ndarray,
    returns:        np.ndarray,
    views:          list[BLView],
    tau:            float = 0.025,
) -> dict:
    """Pipeline completo: prior + views → retornos/cov posteriores prontos
    para serem usados em min-variance ou mean-variance (core/markowitz.py).

    Args:
      tickers:       lista N ordenada de tickers
      prior_returns: π — vetor N de retornos esperados a priori
      returns:       matriz (T, N) de retornos historicos para Σ amostral
      views:         lista de BLView
      tau:           incerteza do prior

    Returns:
      {
        'expected_returns': posterior E[R] vetor N,
        'covariance':       Σ_BL matriz N×N,
        'views_aplicadas':  K,
        'shift_max':        maior |posterior - prior| em pp,
      }
    """
    if returns.ndim != 2 or returns.shape[1] != len(tickers):
        raise ValueError("returns deve ser (T, N) com N = len(tickers)")
    sigma = np.cov(returns, rowvar=False)
    e_post = posterior_returns(prior_returns, sigma, views, tickers, tau)
    s_post = posterior_covariance(sigma, views, tickers, tau)
    shift  = float(np.max(np.abs(e_post - prior_returns)))
    return {
        "expected_returns": e_post,
        "covariance":       s_post,
        "views_aplicadas":  len(views),
        "shift_max":        shift,
    }
