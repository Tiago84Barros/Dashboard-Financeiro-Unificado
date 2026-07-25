"""
core/us_score.py
Score fundamentalista americano — RELATIVO por indústria (não copia pesos do B3).

Metodologia (alinhada ao rigor do B3, adaptada aos EUA):
  1. Winsorização intra-grupo (apara caudas antes de ranquear).
  2. Percentil intra-INDÚSTRIA por métrica (grupo estruturalmente comparável);
     grupos pequenos caem para setor e, por fim, universo (flag de fallback).
  3. Ausência preserva neutralidade estatística, mas reduz explicitamente a
     confiança e puxa a trilha incompleta para 50 (não mascara cobertura baixa).
  4. Seis trilhas de fatores → média das métricas da trilha → soma ponderada → 0–100.
  5. Pesos por trilha ajustáveis por setor (economicamente justificado).

Puro (pandas em memória). Coberto por tests/test_us_score.py.
"""
from __future__ import annotations

import pandas as pd

from core.us_metrics import LOWER_IS_BETTER

# ── Trilhas de fatores ────────────────────────────────────────────────────────
FACTOR_TRACKS: dict[str, list[str]] = {
    # sbc_to_revenue e fcf_ex_sbc_margin entram como QUALIDADE DOS LUCROS: a
    # remuneração em ações é despesa econômica que o FCF GAAP devolve somada,
    # inflando a margem de caixa de quem paga o time em participação.
    "quality": ["gross_margin", "operating_margin", "net_margin", "fcf_margin",
                "cash_conversion", "roe", "roa", "sbc_to_revenue",
                "fcf_ex_sbc_margin"],
    "growth": ["revenue_cagr_3y", "revenue_cagr_5y", "op_income_cagr_3y",
               "eps_cagr_3y", "fcf_cagr_3y"],
    "solidity": ["net_debt_ebitda", "interest_coverage", "current_ratio",
                 "debt_to_equity"],
    "capital_efficiency": ["roic"],
    "valuation": ["earnings_yield", "ev_ebit", "ev_ebitda", "fcf_yield",
                  "p_fcf", "p_s"],
    # Recompra só cria valor se reduzir a base acionária: share_count_cagr_3y
    # é o contraponto ao shareholder_yield (buyback anulado por emissão SBC).
    "shareholder": ["shareholder_yield", "share_count_cagr_3y"],
}

DEFAULT_TRACK_WEIGHTS: dict[str, float] = {
    "quality": 0.22, "growth": 0.18, "solidity": 0.15,
    "capital_efficiency": 0.15, "valuation": 0.18, "shareholder": 0.12,
}

# Ajustes por setor (economicamente justificados). REIT: lucro/EBIT distorcem →
# menos peso em qualidade contábil e valuation por lucro, mais em retorno/solidez.
SECTOR_TRACK_OVERRIDES: dict[str, dict[str, float]] = {
    "Real Estate": {"quality": 0.15, "valuation": 0.12, "shareholder": 0.20,
                    "solidity": 0.20, "growth": 0.18, "capital_efficiency": 0.15},
    "Financial Services": {"quality": 0.24, "valuation": 0.20, "solidity": 0.10,
                           "capital_efficiency": 0.16, "growth": 0.18,
                           "shareholder": 0.12},
}

NEUTRAL = 0.5
_ALL_METRICS = [m for ms in FACTOR_TRACKS.values() for m in ms]

# Uma nota pode ser calculada com informação parcial, mas só é considerada
# decision-grade quando as trilhas essenciais possuem cobertura mínima.
CRITICAL_TRACK_MIN_COVERAGE = {
    "quality": 0.40,
    "growth": 0.40,
    "solidity": 0.25,
    "valuation": 0.50,
}


def _sector_confidence_penalty(sector: object) -> float:
    """Penaliza categorias ainda atendidas por proxies contábeis genéricas."""
    return 0.85 if str(sector or "") in {"Real Estate", "Financial Services"} else 1.0


def _weights_for(sector: str | None) -> dict[str, float]:
    w = dict(DEFAULT_TRACK_WEIGHTS)
    if sector and sector in SECTOR_TRACK_OVERRIDES:
        w.update(SECTOR_TRACK_OVERRIDES[sector])
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}  # renormaliza


def _winsorized_percentile(s: pd.Series, lower: float = 0.05,
                           upper: float = 0.95) -> pd.Series:
    """Winsoriza e devolve o percentil (0–1) na ordem 'maior é melhor'."""
    valid = s.dropna()
    if valid.empty:
        return pd.Series(NEUTRAL, index=s.index)
    lo, hi = valid.quantile(lower), valid.quantile(upper)
    clipped = s.clip(lower=lo, upper=hi)
    pct = clipped.rank(pct=True, method="average")
    return pct.fillna(NEUTRAL)


def _rank_within(df: pd.DataFrame, group_col: str, min_group: int) -> pd.DataFrame:
    """Percentil por métrica dentro do grupo; grupos pequenos usam o universo."""
    out = pd.DataFrame(index=df.index)
    counts = df.groupby(group_col)[group_col].transform("size") if group_col in df else None
    for metric in _ALL_METRICS:
        if metric not in df.columns:
            out[metric] = NEUTRAL
            continue
        col = df[metric]
        if group_col in df.columns:
            ranked = df.groupby(group_col)[metric].transform(_winsorized_percentile)
            # grupos pequenos: rank no universo inteiro
            small = counts < min_group
            if small.any():
                universe = _winsorized_percentile(col)
                ranked = ranked.where(~small, universe)
        else:
            ranked = _winsorized_percentile(col)
        if metric in LOWER_IS_BETTER:
            ranked = 1.0 - ranked
        out[metric] = ranked.fillna(NEUTRAL)
    return out


def score_cross_section(df: pd.DataFrame, *, group_col: str = "industry",
                        min_group: int = 4) -> pd.DataFrame:
    """Calcula o score 0–100 e as trilhas para cada empresa do cross-section.

    df precisa conter 'symbol', 'sector'/'industry' e as colunas de métricas
    (saída de core.us_metrics.compute_company_metrics). Retorna cópia com colunas
    score_<trilha> (0–100), coverage_<trilha> e 'score' (0–100).
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["symbol", "score"])
    df = df.copy().reset_index(drop=True)
    pct = _rank_within(df, group_col, min_group)

    result = df.copy()
    track_scores: dict[str, pd.Series] = {}
    for track, metrics in FACTOR_TRACKS.items():
        present = [m for m in metrics if m in df.columns]
        if present:
            track_scores[track] = pct[present].mean(axis=1)
            # cobertura real = fração de métricas não-ausentes na trilha
            cov = df[present].notna().mean(axis=1)
        else:
            track_scores[track] = pd.Series(NEUTRAL, index=df.index)
            cov = pd.Series(0.0, index=df.index)
        # Uma trilha esparsa não pode produzir convicção extrema. A nota é
        # encolhida para o neutro conforme a raiz da cobertura observada.
        reliability = cov.pow(0.5)
        track_scores[track] = NEUTRAL + (track_scores[track] - NEUTRAL) * reliability
        result[f"score_{track}"] = (track_scores[track] * 100).round(1)
        result[f"coverage_{track}"] = (cov * 100).round(0)

    # score final: soma ponderada por setor (pesos por linha, pois variam)
    def _row_score(i: int) -> float:
        sector = df.at[i, "sector"] if "sector" in df.columns else None
        w = _weights_for(sector)
        return round(sum(w[t] * track_scores[t].iat[i] for t in FACTOR_TRACKS) * 100, 1)

    result["score"] = [_row_score(i) for i in range(len(df))]
    # cobertura global (quantas métricas a empresa tinha, de todas)
    metric_cols = [m for m in _ALL_METRICS if m in df.columns]
    result["coverage"] = (df[metric_cols].notna().mean(axis=1) * 100).round(0) \
        if metric_cols else 0.0
    critical_missing: list[list[str]] = []
    confidence: list[float] = []
    statuses: list[str] = []
    for i in range(len(result)):
        missing = [
            track for track, minimum in CRITICAL_TRACK_MIN_COVERAGE.items()
            if float(result.at[i, f"coverage_{track}"] or 0) / 100.0 < minimum
        ]
        critical_missing.append(missing)
        overall = float(result.at[i, "coverage"] or 0) / 100.0
        critical_ratio = 1.0 - len(missing) / len(CRITICAL_TRACK_MIN_COVERAGE)
        sector = df.at[i, "sector"] if "sector" in df.columns else None
        conf = 100.0 * (0.70 * overall + 0.30 * critical_ratio)
        conf *= _sector_confidence_penalty(sector)
        conf = round(max(0.0, min(100.0, conf)), 1)
        confidence.append(conf)
        if conf >= 75.0 and not missing:
            statuses.append("decision_grade")
        elif conf >= 60.0:
            statuses.append("research_grade")
        else:
            statuses.append("screen_grade")
    result["score_confidence"] = confidence
    result["score_status"] = statuses
    result["critical_missing"] = critical_missing
    return result.sort_values("score", ascending=False).reset_index(drop=True)


def industry_comparison(scored: pd.DataFrame, industry: str) -> pd.DataFrame:
    """Recorta e ordena os pares de uma indústria (aba Comparação por Indústria)."""
    if scored is None or scored.empty or "industry" not in scored.columns:
        return pd.DataFrame()
    peers = scored[scored["industry"] == industry].copy()
    return peers.sort_values("score", ascending=False).reset_index(drop=True)
