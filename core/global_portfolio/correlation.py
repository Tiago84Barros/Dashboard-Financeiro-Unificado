"""Correlacao entre os ativos do patrimonio e diversificacao real.

Contar ativos nao mede diversificacao: onze FIIs de logistica sao uma aposta,
nao onze. A razao de diversificacao e o numero efetivo de apostas respondem a
essa pergunta; a contagem simples nao.

Reaproveita core/b3_correlation_diversification, que ja implementa a matriz com
piso de observacoes e a busca de pares altos.

Coberto por tests/test_global_correlation.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.b3_correlation_diversification import (
    correlation_matrix,
    high_correlation_pairs,
)

LIMIAR_REDUNDANCIA = 0.80


def matriz(retornos: pd.DataFrame) -> pd.DataFrame:
    """Matriz de correlacao dos retornos mensais."""
    if not isinstance(retornos, pd.DataFrame) or retornos.shape[1] < 2:
        return pd.DataFrame()
    return correlation_matrix(retornos)


def pares_redundantes(retornos: pd.DataFrame,
                      limiar: float = LIMIAR_REDUNDANCIA) -> list[tuple[str, str, float]]:
    """Pares acima do limiar, do mais correlacionado ao menos."""
    corr = matriz(retornos)
    if corr.empty:
        return []
    brutos = high_correlation_pairs(corr, threshold=limiar)
    saida = [(str(a), str(b), float(c)) for a, b, c in brutos]
    return sorted(saida, key=lambda t: (-t[2], t[0], t[1]))


def correlacao_media(retornos: pd.DataFrame) -> float | None:
    """Correlacao media entre pares distintos, ou None se nao houver par."""
    corr = matriz(retornos)
    if corr.empty:
        return None
    valores = corr.to_numpy(dtype=float)
    triangulo = valores[np.triu_indices_from(valores, k=1)]
    triangulo = triangulo[~np.isnan(triangulo)]
    return float(triangulo.mean()) if triangulo.size else None


def _pesos_alinhados(retornos: pd.DataFrame, pesos: dict) -> np.ndarray | None:
    if not isinstance(retornos, pd.DataFrame) or retornos.shape[1] < 2:
        return None
    w = np.array([float(pesos.get(c, 0.0)) for c in retornos.columns], dtype=float)
    total = w.sum()
    return (w / total) if total > 0 else None


def razao_diversificacao(retornos: pd.DataFrame, pesos: dict) -> float | None:
    """(soma de wi*sigma_i) / sigma_p. 1,0 = nenhuma diversificacao real."""
    w = _pesos_alinhados(retornos, pesos)
    if w is None:
        return None
    sigmas = retornos.std(ddof=1).to_numpy(dtype=float)
    cov = retornos.cov(ddof=1).to_numpy(dtype=float)
    sigma_p = float(np.sqrt(max(w @ cov @ w, 0.0)))
    if sigma_p <= 0:
        return None
    return float((w * sigmas).sum() / sigma_p)


def apostas_efetivas(retornos: pd.DataFrame, pesos: dict) -> float | None:
    """Numero efetivo de apostas independentes, por PCA da covariancia ponderada.

    Pondera a covariancia pelos pesos (Sigma' = diag(w) @ Sigma @ diag(w)) e
    decompoe em componentes principais: os autovalores de Sigma' sao a
    variancia que cada fator de risco contribui ao portfolio. O inverso do
    HHI dessas contribuicoes da o numero efetivo de apostas — dois ativos
    identicos colapsam para ~1, dois independentes de peso igual dao ~2.

    Nota: projetar o vetor de pesos nos autovetores da covariancia NAO
    ponderada (a alternativa mais obvia) degenera para o caso de 2 ativos com
    pesos iguais e variancias parecidas: qualquer correlacao nao nula (por
    menor que seja) faz os autovetores colapsarem exatamente na base
    simetrica/antissimetrica, zerando um dos componentes e cravando o
    resultado em 1,0 independente do quao correlacionados os ativos
    realmente estao. Ponderar a matriz antes de decompor evita essa
    descontinuidade.
    """
    w = _pesos_alinhados(retornos, pesos)
    if w is None:
        return None
    cov = retornos.cov(ddof=1).to_numpy(dtype=float)
    cov_ponderada = np.outer(w, w) * cov
    autovalores = np.clip(np.linalg.eigvalsh(cov_ponderada), 0.0, None)
    total = autovalores.sum()
    if total <= 0:
        return None
    p = autovalores / total
    return float(1.0 / np.square(p).sum())
