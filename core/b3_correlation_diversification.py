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

# A-135, segunda frente. `correlation_matrix` é pairwise: cada par usa toda a
# sobreposição que tiver. O quadro de preços desta tela vem de
# `_batch_yf_precos_mensais(..., period="10y")`, então um par pode ser medido em
# 120 meses e o par ao lado em 18 — e os dois são confrontados com o MESMO
# limiar de 0,65 e somados na MESMA média por `average_pairwise_correlation`.
#
# Isso não é só imprecisão: a substituição por correlação compara `baseline_avg`
# com `trial_avg` trocando um ativo por outro. Se o candidato tem história curta,
# os pares dele são medidos noutra janela — o "ganho" de diversificação pode vir
# da mudança de AMOSTRA, não da mudança de ativo. A decisão de trocar passa a
# depender de quando o candidato abriu capital.
#
# O teto de 60 meses não elimina a heterogeneidade (quem tem 24 meses continua
# medido em 24), mas reduz a faixa de 18–120 para 18–60 e alinha esta tela com
# `core.correlation_analysis.JANELA_CORR_MESES`, que usa os mesmos 5 anos.
# Homogeneidade completa exigiria interseção, que descartaria candidatos — é o
# que `core.global_portfolio.returns` faz, onde o quadro é publicado e não há
# candidato a preservar.
JANELA_CORR_MESES = 60


def monthly_returns_for(df_precos: pd.DataFrame, tickers: list[str],
                        janela_meses: int | None = JANELA_CORR_MESES) -> pd.DataFrame:
    """Retornos mensais (variação percentual) das colunas presentes em df_precos.

    Trunca aos últimos ``janela_meses`` meses (ver ``JANELA_CORR_MESES``).
    ``None`` devolve a série inteira, para quem precisa de história completa.
    """
    if df_precos is None or df_precos.empty:
        return pd.DataFrame()
    cols = [t for t in dict.fromkeys(tickers) if t in df_precos.columns]
    if not cols:
        return pd.DataFrame()
    precos = df_precos[cols].dropna(how="all")
    rets = precos.pct_change().replace([np.inf, -np.inf], np.nan)
    rets = rets.iloc[1:]
    if janela_meses and janela_meses > 0:
        rets = rets.tail(int(janela_meses))
    return rets


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
                # Orientação canônica (menor ticker primeiro): sem isso o par
                # sai como (A,B) ou (B,A) conforme a ordem das colunas, e quem
                # consome decide o "mais fraco" pelo primeiro em caso de empate
                # exato de score.
                a, b = sorted((str(tickers[i]), str(tickers[j])))
                pairs.append((a, b, float(rho)))
    # Ordenação TOTAL: só por |rho| os empates seguiriam a ordem das colunas,
    # que vem da ordem dos itens da carteira — variável entre processos. Foi a
    # fonte residual de não determinismo medida em 29/07/2026 (GOAU4 vs SHUL4
    # na Siderurgia, com três sementes concordando por acaso).
    pairs.sort(key=lambda p: (-abs(p[2]), str(p[0]), str(p[1])))
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
