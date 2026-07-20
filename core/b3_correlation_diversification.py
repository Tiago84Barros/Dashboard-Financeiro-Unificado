"""
core/b3_correlation_diversification.py — controle de correlação na seleção
final da Criação de Portfólio B3.

Por que existe: cada segmento escolhe seu(s) líder(es) olhando só para os
PRÓPRIOS fundamentos — nunca para o que os outros segmentos já escolheram.
Isso produz diversificação nominal: 5 segmentos diferentes (mineração,
petroquímica, autopeças, elétrica, bens de capital) podem, na prática,
compartilhar o mesmo fator de risco macro (ciclo de commodities), e nada no
motor batia essa correlação antes de aprovar a carteira final.

Este módulo é deliberadamente puro (sem Streamlit, sem I/O): recebe preços já
carregados e devolve matriz de correlação, pares acima de um limiar e um
índice de diversificação (1-HHI). A decisão de SUBSTITUIR um ativo por outro
do mesmo segmento (nunca entre segmentos distintos — isso quebraria a lógica
de "líder do segmento") e o reajuste de pesos por min-variance (Ledoit-Wolf,
via core.markowitz) ficam na orquestração de views/portfolio_b3.py, no mesmo
padrão que _aplicar_gate_qualitativo já usa para o veto por parecer LLM.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_CORR_THRESHOLD = 0.65
MIN_OBS_CORRELACAO = 18  # meses de sobreposição — mesmo piso do walk-forward


def monthly_returns_for(df_precos: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Retornos mensais (variação percentual) das colunas presentes em df_precos."""
    if df_precos is None or df_precos.empty:
        return pd.DataFrame()
    cols = [t for t in dict.fromkeys(tickers) if t in df_precos.columns]
    if not cols:
        return pd.DataFrame()
    precos = df_precos[cols].dropna(how="all")
    rets = precos.pct_change().replace([np.inf, -np.inf], np.nan)
    return rets.iloc[1:]


def correlation_matrix(returns: pd.DataFrame, min_obs: int = MIN_OBS_CORRELACAO) -> pd.DataFrame:
    """Correlação de Pearson pairwise-complete. NaN quando a sobreposição é curta demais."""
    if returns is None or returns.empty or returns.shape[1] < 2:
        return pd.DataFrame()
    return returns.corr(min_periods=min_obs)


def average_pairwise_correlation(corr: pd.DataFrame) -> float:
    """Média dos pares fora da diagonal. 0.0 quando não há pares válidos."""
    if corr is None or corr.empty or corr.shape[0] < 2:
        return 0.0
    values = corr.to_numpy(dtype=float)
    n = values.shape[0]
    offdiag = values[~np.eye(n, dtype=bool)]
    offdiag = offdiag[np.isfinite(offdiag)]
    if offdiag.size == 0:
        return 0.0
    return float(np.mean(offdiag))


def high_correlation_pairs(
    corr: pd.DataFrame, threshold: float = DEFAULT_CORR_THRESHOLD
) -> list[tuple[str, str, float]]:
    """Pares (ticker_a, ticker_b, rho) com |rho| >= threshold, do mais correlacionado ao menos."""
    if corr is None or corr.empty:
        return []
    tickers = list(corr.columns)
    pairs: list[tuple[str, str, float]] = []
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            rho = corr.iloc[i, j]
            if pd.notna(rho) and abs(float(rho)) >= threshold:
                pairs.append((tickers[i], tickers[j], float(rho)))
    pairs.sort(key=lambda p: abs(p[2]), reverse=True)
    return pairs


def correlation_coverage(returns: pd.DataFrame, min_obs: int = MIN_OBS_CORRELACAO) -> tuple[int, int]:
    """(pares com sobreposição suficiente, pares totais) — para decidir se dá para confiar na matriz."""
    if returns is None or returns.empty or returns.shape[1] < 2:
        return (0, 0)
    cols = list(returns.columns)
    total = 0
    ok = 0
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            total += 1
            n_overlap = returns[[cols[i], cols[j]]].dropna().shape[0]
            if n_overlap >= min_obs:
                ok += 1
    return (ok, total)


def diversification_index(weights: dict[str, float]) -> float:
    """1 − HHI dos pesos. 0 = concentrado num único ativo; perto de 1 = espalhado."""
    values = np.array([v for v in weights.values() if np.isfinite(v) and v > 0], dtype=float)
    if values.size == 0:
        return 0.0
    values = values / values.sum()
    return float(1.0 - (values ** 2).sum())
