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
    MIN_OBS_CORRELACAO,
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


def _covariancia_confiavel(retornos: pd.DataFrame,
                           min_obs: int = MIN_OBS_CORRELACAO) -> pd.DataFrame:
    """Covariancia com o mesmo piso de sobreposicao que a matriz de correlacao.

    Dois ativos podem ter historico individual longo e mesmo assim se cruzarem
    por poucos meses: a covariancia desse par e ruido. `matriz()` ja recusa esse
    par (o piso `MIN_OBS_CORRELACAO` vira NaN em `correlation_matrix`); sem o
    mesmo piso aqui, o par entrava calado na razao de diversificacao e no PCA
    das apostas efetivas, que sao exibidos como fato.

    Com o piso, o par reprovado vira NaN — e NaN contamina tanto `w @ cov @ w`
    quanto a decomposicao espectral, o que apareceria como `nan` na tela. Entao
    o ativo e descartado do calculo: melhor medir a diversificacao dos ativos
    que se sobrepoem o bastante do que devolver um numero invalido. Remove um
    por vez, sempre o de mais pares reprovados (desempate pelo nome, para o
    resultado nao depender da ordem das colunas). Devolve quadro vazio quando
    nao sobra nenhum par confiavel.
    """
    cov = retornos.cov(ddof=1, min_periods=min_obs)
    while cov.shape[0] >= 2 and bool(cov.isna().to_numpy().any()):
        reprovados = cov.isna().sum(axis=1)
        pior = min(reprovados.index, key=lambda c: (-int(reprovados[c]), str(c)))
        restantes = [c for c in cov.columns if c != pior]
        cov = cov.loc[restantes, restantes]
    return cov if cov.shape[0] >= 2 else pd.DataFrame()


def _cov_e_pesos(retornos: pd.DataFrame,
                 pesos: dict) -> tuple[pd.DataFrame | None, np.ndarray | None]:
    """Covariancia confiavel e pesos renormalizados sobre os ativos que sobraram."""
    if not isinstance(retornos, pd.DataFrame) or retornos.shape[1] < 2:
        return None, None
    cov = _covariancia_confiavel(retornos)
    if cov.empty:
        return None, None
    w = np.array([float(pesos.get(c, 0.0)) for c in cov.columns], dtype=float)
    total = w.sum()
    if total <= 0:
        return None, None
    return cov, w / total


def razao_diversificacao(retornos: pd.DataFrame, pesos: dict) -> float | None:
    """(soma de wi*sigma_i) / sigma_p. 1,0 = nenhuma diversificacao real.

    Calculada apenas sobre os ativos com sobreposicao suficiente entre si
    (ver `_covariancia_confiavel`); pesos renormalizados sobre esse subconjunto.
    """
    cov_df, w = _cov_e_pesos(retornos, pesos)
    if cov_df is None:
        return None
    sigmas = retornos[list(cov_df.columns)].std(ddof=1).to_numpy(dtype=float)
    cov = cov_df.to_numpy(dtype=float)
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

    Como a razao de diversificacao, considera apenas os ativos com sobreposicao
    suficiente entre si (ver `_covariancia_confiavel`).
    """
    cov_df, w = _cov_e_pesos(retornos, pesos)
    if cov_df is None:
        return None
    cov = cov_df.to_numpy(dtype=float)
    cov_ponderada = np.outer(w, w) * cov
    autovalores = np.clip(np.linalg.eigvalsh(cov_ponderada), 0.0, None)
    total = autovalores.sum()
    if total <= 0:
        return None
    p = autovalores / total
    return float(1.0 / np.square(p).sum())


def correlacao_media_por_ativo(retornos: pd.DataFrame) -> dict[str, float]:
    """Correlacao media de cada ativo com os DEMAIS, sem a diagonal.

    Correlacao media baixa e a evidencia de que o ativo diversifica de fato,
    e nao apenas de que tem nome diferente. A diagonal fica de fora porque
    1.0 contra si mesmo inflaria todo mundo igualmente.

    Ativo cuja linha nao tem nenhum par valido fica ausente do resultado —
    devolver 0.0 diria "nao se correlaciona com nada", que e o oposto de
    "nao sabemos".
    """
    m = matriz(retornos)
    if m.empty or len(m.columns) < 2:
        return {}

    saida: dict[str, float] = {}
    for simbolo in sorted(m.columns):
        outros = m.loc[simbolo, [c for c in m.columns if c != simbolo]]
        media = pd.to_numeric(outros, errors="coerce").mean()
        if pd.notna(media):
            saida[str(simbolo)] = float(media)
    return saida
