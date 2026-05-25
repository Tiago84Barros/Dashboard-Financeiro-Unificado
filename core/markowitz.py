"""
core/markowitz.py — otimização de portfólio mean-variance restrita.

Implementa a recomendação C4 do parecer da banca examinadora (2026-05-23):
após a seleção dos top-N por score, aplicar otimização Markowitz restrita
para gerar pesos que minimizam variância sujeitos a cap por ativo.

Lacuna identificada pela banca: o gamma-tilt + cap-soft atual ignora
correlações entre ativos. Cenário típico: engine seleciona BBAS3 + ITUB4
+ SANB3 (todos bancos, ρ ≈ 0.85) com pesos proporcionais ao score, o
que produz risco efetivo muito maior que o desejado.

Implementação:
  • Estima covariância via Ledoit-Wolf shrinkage (estável em janelas curtas)
  • Otimiza minimum-variance com restrições: 0 ≤ w_i ≤ cap, Σw = 1
  • Quadratic programming via cvxpy (se disponível) ou aproximação numérica

Para portfólios pequenos (N ≤ 10), uma aproximação numérica baseada em
matrix inversion é suficiente e dispensa cvxpy.

Referências:
  - Markowitz (1952) "Portfolio Selection", JF 7(1)
  - Ledoit & Wolf (2003) "Improved Estimation of Covariance Matrix"
  - Boyd & Vandenberghe (2004) Convex Optimization, cap. 4
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ──────────────────────────────────────────────────────────────────────────
# Estimação de covariância
# ──────────────────────────────────────────────────────────────────────────

def ledoit_wolf_shrinkage(
    returns: np.ndarray,
    target: str = "diagonal",
) -> tuple[np.ndarray, float]:
    """
    Estima matriz de covariância com Ledoit-Wolf shrinkage.

    Σ_shrink = (1-α) Σ_sample + α F

    onde α ∈ [0, 1] é o coeficiente de shrinkage estimado em
    Ledoit & Wolf (2003) e F é uma matriz target (default: diagonal
    da Σ_sample com correlações zeradas — assume independência fora
    da diagonal como prior).

    Reduz erro de estimação em janelas pequenas (N < 100 observações
    com K > 20 ativos), problema clássico de Markowitz com covariância
    amostral pura.

    Args:
      returns: array (T × K) de retornos
      target:  "diagonal" (zera correlações) ou "identity" (matriz unit)

    Returns:
      (Σ_shrink, α): matriz K×K e coeficiente de shrinkage usado
    """
    T, K = returns.shape
    if T < 2 or K < 2:
        return np.eye(K), 1.0

    sample_cov = np.cov(returns, rowvar=False, ddof=1)

    if target == "diagonal":
        F = np.diag(np.diag(sample_cov))
    else:  # identity (scaled)
        avg_var = np.mean(np.diag(sample_cov))
        F = avg_var * np.eye(K)

    # Estimador simplificado de α (Ledoit-Wolf 2004, eq. 6)
    # Versão completa requer estimar pi, rho, gamma — aqui usamos
    # heurística baseada na razão entre off-diagonal variance e total.
    off_diag_var = (sample_cov - F).flatten().var()
    total_var    = sample_cov.flatten().var() or 1.0
    alpha = np.clip(off_diag_var / total_var, 0.0, 1.0)

    sigma_shrink = (1 - alpha) * sample_cov + alpha * F
    return sigma_shrink, float(alpha)


# ──────────────────────────────────────────────────────────────────────────
# Otimização min-variance restrita
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class MarkowitzResult:
    """Resultado da otimização min-variance restrita."""
    weights:           dict[str, float]
    expected_variance: float
    expected_std:      float
    diversification:   float    # 1 = totalmente diversificado, 0 = single asset
    converged:         bool
    method:            str      # "cvxpy" | "numerical" | "fallback_equal"


def min_variance_capped(
    tickers:    list[str],
    returns:    np.ndarray,
    cap:        float = 0.30,
    shrinkage:  bool  = True,
) -> MarkowitzResult:
    """
    Calcula pesos min-variance com restrição 0 ≤ w_i ≤ cap, Σw = 1.

    Problema:
        min   w'Σw
        s.a.  Σw = 1
              0 ≤ w_i ≤ cap

    Args:
      tickers: lista K de tickers
      returns: array (T × K) de retornos históricos (mensais ou diários)
      cap:     peso máximo por ativo (0.30 = 30%)
      shrinkage: se True, usa Ledoit-Wolf na covariância

    Returns:
      MarkowitzResult com pesos otimizados.
    """
    K = len(tickers)
    if K == 0 or returns.size == 0:
        return MarkowitzResult(
            weights={}, expected_variance=0.0, expected_std=0.0,
            diversification=0.0, converged=False, method="fallback_equal",
        )
    if K == 1:
        return MarkowitzResult(
            weights={tickers[0]: 1.0},
            expected_variance=float(returns.var()),
            expected_std=float(returns.std()),
            diversification=0.0, converged=True, method="single_asset",
        )

    # Estima covariância
    if shrinkage:
        cov, _ = ledoit_wolf_shrinkage(returns)
    else:
        cov = np.cov(returns, rowvar=False, ddof=1)

    # Tenta cvxpy primeiro (solução exata)
    try:
        import cvxpy as cp
        w = cp.Variable(K)
        prob = cp.Problem(
            cp.Minimize(cp.quad_form(w, cp.psd_wrap(cov))),
            [cp.sum(w) == 1, w >= 0, w <= cap],
        )
        prob.solve()
        if prob.status == "optimal" and w.value is not None:
            w_arr = np.array(w.value).flatten()
            w_arr = np.clip(w_arr, 0, cap)
            w_arr = w_arr / w_arr.sum() if w_arr.sum() > 0 else np.ones(K) / K
            var = float(w_arr @ cov @ w_arr)
            div = 1.0 - (w_arr ** 2).sum()  # 1 - HHI
            return MarkowitzResult(
                weights={t: float(w_arr[i]) for i, t in enumerate(tickers)},
                expected_variance=var,
                expected_std=float(np.sqrt(max(var, 0))),
                diversification=float(div),
                converged=True, method="cvxpy",
            )
    except Exception:
        pass  # fallback numérico

    # Fallback: solução analítica unconstrained + projeção
    # w_unc = Σ⁻¹ 1 / (1' Σ⁻¹ 1)  (mínima variância sem restrições)
    try:
        ones = np.ones(K)
        inv = np.linalg.pinv(cov + 1e-8 * np.eye(K))
        w = (inv @ ones) / (ones @ inv @ ones)
        # Projeta para [0, cap]
        w = np.clip(w, 0.0, cap)
        # Renormaliza
        if w.sum() <= 0:
            w = np.ones(K) / K
        else:
            w = w / w.sum()
        # Itera projeção (caso algum exceda cap após normalização)
        for _ in range(20):
            excess = np.maximum(w - cap, 0).sum()
            if excess < 1e-9:
                break
            w = np.clip(w, 0.0, cap)
            slack = (1.0 - w.sum())
            below = w < cap
            n_below = below.sum() or 1
            w[below] += slack / n_below
            w = np.clip(w, 0.0, cap)
        var = float(w @ cov @ w)
        div = 1.0 - (w ** 2).sum()
        return MarkowitzResult(
            weights={t: float(w[i]) for i, t in enumerate(tickers)},
            expected_variance=var,
            expected_std=float(np.sqrt(max(var, 0))),
            diversification=float(div),
            converged=True, method="numerical",
        )
    except Exception:
        # Último recurso: equal weight respeitando cap
        w_eq = min(1.0 / K, cap)
        w = np.array([w_eq] * K)
        w = w / w.sum()
        var = float(w @ cov @ w) if cov.size > 0 else 0.0
        return MarkowitzResult(
            weights={t: float(w[i]) for i, t in enumerate(tickers)},
            expected_variance=var,
            expected_std=float(np.sqrt(max(var, 0))),
            diversification=1.0 - (w ** 2).sum(),
            converged=False, method="fallback_equal",
        )


# ──────────────────────────────────────────────────────────────────────────
# Helper: pesos híbridos score × min-variance
# ──────────────────────────────────────────────────────────────────────────

def pesos_hibridos_score_markowitz(
    score_weights: dict[str, float],
    markowitz_res: MarkowitzResult,
    alpha:         float = 0.50,
) -> dict[str, float]:
    """
    Combina pesos do gamma-tilt (score-proportional) com pesos min-variance.

        w_final = α × w_score + (1-α) × w_markowitz

    Permite ao usuário escolher quanto peso dar à seleção fundamentalista
    (α alto) vs ao controle de risco via covariância (α baixo).

    α = 1.0 → pesos puros do score (comportamento legado)
    α = 0.0 → pesos puros min-variance (ignora score)
    α = 0.5 → meio termo recomendado
    """
    tickers = set(score_weights) | set(markowitz_res.weights)
    combined = {}
    for tk in tickers:
        w_sc = score_weights.get(tk, 0.0)
        w_mk = markowitz_res.weights.get(tk, 0.0)
        combined[tk] = alpha * w_sc + (1 - alpha) * w_mk
    total = sum(combined.values()) or 1.0
    return {tk: w / total for tk, w in combined.items()}
