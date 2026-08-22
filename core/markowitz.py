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
    Estima matriz de covariância com shrinkage de Ledoit-Wolf.

    Σ_shrink = (1-α) S + α F,  α ∈ [0, 1]

    A intensidade α usa a forma fechada do estimador well-conditioned de
    Ledoit & Wolf (2004, J. Multivariate Analysis 88): α* = b²/d², onde
    b² estima o erro de amostragem de S (decresce ~1/T: mais observações
    ⇒ menos shrinkage) e d² é a distância de S ao alvo F. Para
    target="identity" F = m·I (fórmula original LW04); para
    target="diagonal" F = diag(S) — mesma intensidade b²/d² recalculada
    contra o alvo estruturado, receita de Schäfer & Strimmer (2005).

    Correção da auditoria 2026-07: a versão anterior usava uma heurística
    ad hoc (var off-diagonal / var total) que não dependia de T e era
    exibida na UI como "Ledoit-Wolf" sem sê-lo.

    Args:
      returns: array (T × K) de retornos
      target:  "diagonal" (preserva variâncias, encolhe correlações) ou
               "identity" (LW 2004 original)

    Returns:
      (Σ_shrink, α): matriz K×K (convenção amostral ddof=1) e α usado
    """
    returns = np.asarray(returns, dtype=float)
    T, K = returns.shape
    if T < 2 or K < 2:
        return np.eye(max(K, 1)), 1.0

    X = returns - returns.mean(axis=0, keepdims=True)
    S = (X.T @ X) / T                      # MLE (ddof=0) — convenção das fórmulas LW

    if target == "diagonal":
        F = np.diag(np.diag(S))
    else:  # identity escalada: F = m·I com m = variância média
        F = (np.trace(S) / K) * np.eye(K)

    # d² = ||S − F||²_F / K   (quanto a amostra difere do alvo)
    d2 = float(((S - F) ** 2).sum()) / K
    # b̄² = (1/T²) Σ_t ||x_t x_tᵀ − S||²_F / K   (erro de estimação de S)
    b2_bar = 0.0
    for t in range(T):
        xt = X[t][:, None]
        b2_bar += float(((xt @ xt.T - S) ** 2).sum())
    b2_bar /= (T ** 2) * K
    b2 = min(b2_bar, d2)

    alpha = 0.0 if d2 <= 0 else float(np.clip(b2 / d2, 0.0, 1.0))
    sigma_shrink = (1 - alpha) * S + alpha * F
    # Reescala MLE → amostral (ddof=1), convenção do restante do módulo
    sigma_shrink *= T / max(T - 1, 1)
    return sigma_shrink, alpha


# ──────────────────────────────────────────────────────────────────────────
# Otimização min-variance restrita
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class MarkowitzResult:
    """Resultado da otimização min-variance restrita.

    method:
      "cvxpy" / "cvxpy_ff"          — QP exato via cvxpy (se instalado)
      "slsqp" / "slsqp_ff"          — QP exato via scipy SLSQP
      "heuristic_projection[_ff]"   — projeção clip+redistribuição da solução
                                      irrestrita; NÃO é o ótimo do problema
                                      restrito (converged=False por honestidade)
      "fallback_equal"              — equal weight (último recurso)
    """
    weights:           dict[str, float]
    expected_variance: float
    expected_std:      float
    diversification:   float    # 1 = totalmente diversificado, 0 = single asset
    converged:         bool
    method:            str


def _solve_qp_min_variance(cov: np.ndarray, cap: float) -> tuple[np.ndarray, str, bool]:
    """Resolve  min w'Σw  s.a.  Σw = 1, 0 ≤ w ≤ cap.

    Cadeia de solvers: cvxpy (se instalado) → scipy SLSQP (em
    requirements.txt) → projeção heurística clip+redistribuição, que NÃO
    é o ótimo do problema restrito e por isso retorna converged=False.
    Retorna (w, method, converged).
    """
    from core.portfolio_constraints import validate_cap_feasibility

    K = cov.shape[0]
    validate_cap_feasibility(K, cap)
    cap_eff = float(cap)

    # 1) cvxpy — QP exato (opcional; não está em requirements.txt)
    try:
        import cvxpy as cp
        w = cp.Variable(K)
        prob = cp.Problem(
            cp.Minimize(cp.quad_form(w, cp.psd_wrap(cov))),
            [cp.sum(w) == 1, w >= 0, w <= cap_eff],
        )
        prob.solve()
        if prob.status == "optimal" and w.value is not None:
            w_arr = np.clip(np.asarray(w.value).flatten(), 0.0, cap_eff)
            s = w_arr.sum()
            if s > 0:
                return w_arr / s, "cvxpy", True
    except Exception:
        pass

    # 2) scipy SLSQP — QP exato (fix auditoria 2026-07: antes daqui o código
    #    caía direto na projeção heurística e a reportava como convergida)
    try:
        from scipy.optimize import minimize as _sp_minimize
        x0 = np.full(K, 1.0 / K)
        res = _sp_minimize(
            lambda w_: float(w_ @ cov @ w_),
            x0,
            jac=lambda w_: 2.0 * (cov @ w_),
            method="SLSQP",
            bounds=[(0.0, cap_eff)] * K,
            constraints=[{"type": "eq",
                          "fun": lambda w_: float(w_.sum() - 1.0),
                          "jac": lambda w_: np.ones(K)}],
            options={"maxiter": 300, "ftol": 1e-12},
        )
        if res.success and res.x is not None:
            w_arr = np.clip(np.asarray(res.x, dtype=float), 0.0, cap_eff)
            s = w_arr.sum()
            if s > 0:
                return w_arr / s, "slsqp", True
    except Exception:
        pass

    # 3) Projeção heurística da solução irrestrita — não-ótima (honestidade:
    #    converged=False; a UI exibe o método efetivamente usado)
    ones = np.ones(K)
    inv = np.linalg.pinv(cov + 1e-8 * np.eye(K))
    w = (inv @ ones) / (ones @ inv @ ones)
    from core.portfolio_constraints import project_capped_simplex
    projected = project_capped_simplex(
        {str(i): float(value) for i, value in enumerate(w)},
        cap_eff,
    )
    w = np.asarray([projected[str(i)] for i in range(K)])
    return w, "heuristic_projection", False


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
    from core.portfolio_constraints import validate_cap_feasibility
    validate_cap_feasibility(K, cap)
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

    # Cadeia de solvers: cvxpy → SLSQP → projeção heurística (não-ótima)
    try:
        w_arr, method, converged = _solve_qp_min_variance(cov, cap)
        var = float(w_arr @ cov @ w_arr)
        return MarkowitzResult(
            weights={t: float(w_arr[i]) for i, t in enumerate(tickers)},
            expected_variance=var,
            expected_std=float(np.sqrt(max(var, 0))),
            diversification=float(1.0 - (w_arr ** 2).sum()),
            converged=converged, method=method,
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
# Min-variance com covariância pré-computada (ex.: Fama-French estruturada)
# ──────────────────────────────────────────────────────────────────────────

def min_variance_with_cov(
    tickers:    list[str],
    cov_matrix: np.ndarray,
    cap:        float = 0.30,
) -> MarkowitzResult:
    """
    Min-variance restrita usando covariância pré-computada (M1 banca).

    Idêntica a min_variance_capped mas recebe Σ diretamente, permitindo
    usar a covariância estruturada do modelo Fama-French (Σ = BΣ_FB' + D)
    em vez de estimá-la via Ledoit-Wolf a partir dos retornos amostral.

    Problema:
        min   w'Σw
        s.a.  Σw = 1
              0 ≤ w_i ≤ cap

    Args:
      tickers:    lista K de tickers (deve coincidir com ordem de cov_matrix)
      cov_matrix: array K×K, covariância estruturada FF ou qualquer outra
      cap:        peso máximo por ativo (0.30 = 30%)

    Returns:
      MarkowitzResult com pesos otimizados e method="cvxpy_ff" ou "numerical_ff".
    """
    K = len(tickers)
    cov = np.asarray(cov_matrix, dtype=float)
    if K == 0 or cov.size == 0:
        return MarkowitzResult(
            weights={}, expected_variance=0.0, expected_std=0.0,
            diversification=0.0, converged=False, method="fallback_equal",
        )
    from core.portfolio_constraints import validate_cap_feasibility
    validate_cap_feasibility(K, cap)
    if K == 1:
        var = float(cov[0, 0]) if cov.size > 0 else 0.0
        return MarkowitzResult(
            weights={tickers[0]: 1.0},
            expected_variance=var,
            expected_std=float(np.sqrt(max(var, 0))),
            diversification=0.0, converged=True, method="single_asset",
        )

    # Regularização mínima para garantir PSD
    cov = cov + 1e-8 * np.eye(K)

    # Cadeia de solvers: cvxpy → SLSQP → projeção heurística (não-ótima)
    try:
        w_arr, method, converged = _solve_qp_min_variance(cov, cap)
        var = float(w_arr @ cov @ w_arr)
        return MarkowitzResult(
            weights={t: float(w_arr[i]) for i, t in enumerate(tickers)},
            expected_variance=var,
            expected_std=float(np.sqrt(max(var, 0))),
            diversification=float(1.0 - (w_arr ** 2).sum()),
            converged=converged, method=method + "_ff",
        )
    except Exception:
        w_eq = min(1.0 / K, cap)
        w = np.full(K, w_eq) / (K * w_eq)
        var = float(w @ cov @ w)
        return MarkowitzResult(
            weights={t: float(w[i]) for i, t in enumerate(tickers)},
            expected_variance=var,
            expected_std=float(np.sqrt(max(var, 0))),
            diversification=float(1.0 - (w ** 2).sum()),
            converged=False, method="fallback_equal",
        )


# ──────────────────────────────────────────────────────────────────────────
# Helper: pesos híbridos score × min-variance
# ──────────────────────────────────────────────────────────────────────────

def pesos_hibridos_score_markowitz(
    score_weights: dict[str, float],
    markowitz_res: MarkowitzResult,
    alpha:         float = 0.50,
    cap:           float | None = None,
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
    normalized = {tk: w / total for tk, w in combined.items()}
    if cap is None:
        return normalized
    from core.portfolio_constraints import project_capped_simplex
    return project_capped_simplex(normalized, cap)
