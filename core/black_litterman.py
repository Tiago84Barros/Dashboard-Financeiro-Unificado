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


# ──────────────────────────────────────────────────────────────────────────
# M3+ (2026-05-27): Integração BL → Markowitz solver
# ──────────────────────────────────────────────────────────────────────────

def _project_capped_simplex(w_target: np.ndarray, cap: float) -> np.ndarray:
    """Projeta vetor w_target no simplex restrito {w >= 0, sum(w)=1, w_i <= cap}.

    Algoritmo iterativo robusto:
      1. Clipa em [0, cap]
      2. Identifica tickers "lockados" no cap
      3. Redistribui o restante (1 - soma_lockados) proporcionalmente
         aos pesos originais dos tickers livres (incluindo zeros, com
         fallback para uniforme se todos forem zero)
      4. Repete enquanto algum free estourar cap (raro com bons inputs)

    Se cap * N < 1, retorna uniforme cap*N (impossível atingir 100%
    com restrição — todos vão pro cap).
    """
    n = len(w_target)
    if cap * n < 0.999:
        # Impossível somar 1 respeitando o cap → tudo no cap (sum < 1)
        # Renormaliza para forçar sum=1, mas isso viola cap. Aceita.
        out = np.full(n, cap)
        return out / out.sum()

    w = np.clip(w_target, 0.0, None)
    if w.sum() <= 1e-12:
        w = np.full(n, 1.0 / n)

    for _ in range(50):  # convergência típica em 2-3 iters
        # Locked = at cap
        locked_mask = w >= cap - 1e-12
        if not locked_mask.any():
            # Sem locked, basta normalizar
            return w / w.sum()
        w[locked_mask] = cap
        remaining = 1.0 - cap * locked_mask.sum()
        free_mask = ~locked_mask
        if not free_mask.any():
            # Todos lockados — soma <= cap*n; força sum=1 violando cap
            return w / w.sum()
        if remaining <= 1e-12:
            # Nada para distribuir; zera frees
            w[free_mask] = 0.0
            return w / w.sum() if w.sum() > 0 else w
        # Distribui proporcional aos w_target originais dos frees
        target_free = w_target[free_mask]
        target_free = np.clip(target_free, 0.0, None)
        if target_free.sum() <= 1e-12:
            w[free_mask] = remaining / free_mask.sum()
        else:
            w[free_mask] = target_free / target_free.sum() * remaining
        # Verifica se algum free estourou cap
        if (w[free_mask] > cap + 1e-12).any():
            continue  # próxima iteração lockará mais
        return w
    # Fallback: renormaliza
    return w / w.sum() if w.sum() > 0 else np.full(n, 1.0 / n)


def apply_bl_to_markowitz(
    tickers:        list[str],
    prior_returns:  np.ndarray,
    returns:        np.ndarray,
    views:          list[BLView],
    tau:            float = 0.025,
    cap:            float = 0.30,
    risk_aversion:  float = 2.5,
    periods_per_year: int = 252,
) -> dict:
    """Fecha o ciclo BL → Markowitz: posterior_returns + cov_BL → pesos
    mean-variance restritos.

    Estratégia (Litterman 1992, equação fundamental):

        w_optimal = (δ × Σ_BL)^(-1) × E[R_BL]

    Com normalização para Σw=1 e restrição 0 ≤ w_i ≤ cap aplicada via
    projeção sobre o simplex restrito (algoritmo iterativo locking +
    redistribuição proporcional), evitando dependência de cvxpy externo.

    Args:
      tickers:       lista N ordenada
      prior_returns: π vetor N (RETORNOS ANUAIS esperados a priori)
      returns:       matriz (T, N) de retornos históricos para Σ amostral —
                     a FREQUÊNCIA deve casar com periods_per_year (diário→252,
                     mensal→12; auditoria 2026-07: mensal com 252 inflava a
                     vol anualizada ~4,6x)
      views:         lista BLView (também em escala anual)
      tau:           incerteza prior (0.025 default)
      cap:           limite por ativo (0.30 = 30%)
      risk_aversion: δ — parâmetro CARA (2.5 típico equity premium ~6%)
      periods_per_year: 252 (diário) / 12 (mensal). Σ é anualizada antes
                        do solver para que E[R] e Σ estejam consistentes.

    Returns:
      {
        'weights':           {ticker: peso},
        'expected_returns':  posterior E[R] anualizado,
        'covariance':        Σ_BL anualizada,
        'expected_portfolio_return': w'·E[R_BL] anualizado,
        'expected_portfolio_vol':    sqrt(w'·Σ_BL·w) anualizada,
        'sharpe_implicit':   E[R_p] / σ_p (ambos anualizados),
        'views_aplicadas':   K,
        'shift_max':         maior |posterior - prior| em pp,
      }
    """
    bl = bl_combined_optimization(tickers, prior_returns, returns, views, tau)
    e_post = bl["expected_returns"]
    # Anualiza covariância para ficar consistente com E[R] anuais
    s_post = bl["covariance"] * float(periods_per_year)

    # Mean-variance unrestricted: w_opt ∝ Σ^(-1) × E[R]
    try:
        sigma_inv = np.linalg.pinv(s_post)
    except np.linalg.LinAlgError:
        sigma_inv = np.linalg.pinv(s_post + 1e-6 * np.eye(s_post.shape[0]))

    w_raw = sigma_inv @ e_post / max(float(risk_aversion), 1e-6)
    # Forçar não-negativo + projetar no simplex restrito
    w = _project_capped_simplex(np.clip(w_raw, 0.0, None), cap)

    weights_dict = {tk: float(w[i]) for i, tk in enumerate(tickers)}
    port_return = float(w @ e_post)
    port_vol = float(np.sqrt(max(w @ s_post @ w, 0.0)))
    sharpe = port_return / port_vol if port_vol > 1e-9 else 0.0

    return {
        "weights":                    weights_dict,
        "expected_returns":           e_post,
        "covariance":                 s_post,
        "expected_portfolio_return":  port_return,
        "expected_portfolio_vol":     port_vol,
        "sharpe_implicit":            sharpe,
        "views_aplicadas":            bl["views_aplicadas"],
        "shift_max":                  bl["shift_max"],
    }
