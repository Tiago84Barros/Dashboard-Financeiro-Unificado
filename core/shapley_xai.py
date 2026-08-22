"""
core/shapley_xai.py — atribuição via Shapley values dos engines do score.

Implementa a recomendação M4 do parecer da banca examinadora (2026-05-23):
substituir explicação qualitativa (`_explicar_score_entrada`) por
atribuição quantitativa rigorosa via Shapley values — princípio padrão
de Explainable AI (XAI) em finanças.

Motivação teórica:
  Lundberg & Lee (2017, "A Unified Approach to Interpreting Model
  Predictions", NeurIPS) demonstram que Shapley é a ÚNICA atribuição
  satisfazendo simultaneamente os quatro axiomas:
    1. Efficiency:    Σ φ_i = score_total − baseline
    2. Symmetry:      engines simétricos têm φ iguais
    3. Dummy:         engine sem efeito tem φ = 0
    4. Additivity:    φ aditivo em composições

  Diferentemente do "_explicar_score_entrada" atual (regras IF-THEN
  qualitativas), Shapley quantifica EXATAMENTE quantos pontos do
  score_entrada vieram de cada engine.

Vantagem prática (4 engines):
  4 engines → 2^4 = 16 coalições. Custo computacional trivial.
  Para 10+ features sería preciso amostragem Monte-Carlo
  (KernelSHAP), mas com 4 fazemos enumeração exata.

API mínima:
  compute_shapley_engines(row) → dict com contribuição de cada engine
  explain_score_entrada_shapley(row) → list[(label, phi, sinal)] para UI

Compatível com saída de `_compute_score_entrada` em empresas_b3.py.
"""
from __future__ import annotations

from itertools import combinations
from math import factorial

# Os 4 engines + 1 baseline (score_base). Ordem documenta a composição:
#   score_entrada = 0.60·score_base + 0.20·q + 0.10·c + 0.10·cq − r_penalty
ENGINES = ["score_base", "q_bonus", "c_bonus", "cq_bonus", "r_penalty"]
WEIGHTS = {
    "score_base": 0.60,
    "q_bonus":    0.20,
    "c_bonus":    0.10,
    "cq_bonus":   0.10,
    "r_penalty": -1.00,   # subtrai diretamente
}

# Baseline neutro: cada engine "ausente" assume valor 50/100 (neutro)
NEUTRAL = {
    "score_base": 50.0,
    "q_bonus":    50.0,
    "c_bonus":    50.0,
    "cq_bonus":   50.0,
    "r_penalty":   0.0,
}


def _v(coalition: tuple[str, ...], row: dict) -> float:
    """Função de valor v(S): score_entrada considerando APENAS engines em S.

    Engines fora da coalição assumem o valor NEUTRAL (não contribuem
    versus baseline). Clamped em [0, 100] como o score real.
    """
    score = 0.0
    for eng in ENGINES:
        val = float(row.get(eng, NEUTRAL[eng])) if eng in coalition \
              else NEUTRAL[eng]
        score += WEIGHTS[eng] * val
    return max(0.0, min(100.0, score))


def compute_shapley_engines(row: dict) -> dict[str, float]:
    """Calcula Shapley value de cada engine versus baseline neutro.

    Args:
      row: dict com keys score_base, q_bonus, c_bonus, cq_bonus, r_penalty
           (saída de _compute_score_entrada por ticker)

    Returns:
      dict {engine: phi} onde phi é a contribuição marginal média do engine
      ao score_entrada. Soma dos phi == score_entrada(row) − v_baseline.
    """
    n = len(ENGINES)
    phi: dict[str, float] = {e: 0.0 for e in ENGINES}

    # Itera sobre todas as coalições S e calcula contribuição marginal
    # de cada engine i quando adicionado a S (i ∉ S).
    # phi_i = sum over S of |S|!*(n-|S|-1)! / n! * (v(S ∪ {i}) - v(S))
    for i, eng in enumerate(ENGINES):
        outros = [e for e in ENGINES if e != eng]
        for s in range(0, n):
            for subset in combinations(outros, s):
                S      = subset
                S_plus = subset + (eng,)
                weight = factorial(s) * factorial(n - s - 1) / factorial(n)
                phi[eng] += weight * (_v(S_plus, row) - _v(S, row))

    return {e: round(phi[e], 2) for e in ENGINES}


def explain_score_entrada_shapley(row: dict) -> list[tuple[str, float, str]]:
    """Versão Shapley do `_explicar_score_entrada` — quantitativa.

    Returns lista de (label, phi_value, sinal) ordenada por |phi| desc.
    Sinal:
      'positivo' se phi >= +2.0
      'negativo' se phi <= -2.0
      'neutro'   caso contrário
    """
    phis = compute_shapley_engines(row)
    labels = {
        "score_base": "Score Base (v2)",
        "q_bonus":    "Quality Engine",
        "c_bonus":    "Consistency Engine",
        "cq_bonus":   "Cash Quality Engine",
        "r_penalty":  "Risk Penalty",
    }
    result = []
    for eng in ENGINES:
        phi = phis[eng]
        if phi >= 2.0:
            sinal = "positivo"
        elif phi <= -2.0:
            sinal = "negativo"
        else:
            sinal = "neutro"
        result.append((labels[eng], phi, sinal))
    # Ordena por magnitude (mais influente primeiro)
    result.sort(key=lambda t: -abs(t[1]))
    return result


def shapley_sanity_check(row: dict, tol: float = 0.5) -> dict:
    """Verifica axioma de Efficiency: Σ φ ≈ score_real − baseline.

    Returns:
      {efficient: bool, residual: float, score_real, baseline, sum_phi}
    """
    score_real = _v(tuple(ENGINES), row)
    baseline   = _v((), row)
    phis = compute_shapley_engines(row)
    sum_phi = sum(phis.values())
    residual = abs(score_real - baseline - sum_phi)
    return {
        "efficient":  residual < tol,
        "residual":   round(residual, 3),
        "score_real": round(score_real, 2),
        "baseline":   round(baseline, 2),
        "sum_phi":    round(sum_phi, 2),
    }
