"""Concentracao do patrimonio por ativo, setor, pais, moeda e classe.

O HHI e publicado tambem como NUMERO EFETIVO DE POSICOES (1/HHI) porque o
indice cru nao e legivel: "HHI 0,30" nao diz nada, "equivale a 3,3 posicoes
iguais" diz.

Coberto por tests/test_global_concentration.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIMENSOES: tuple[str, ...] = ("asset_class", "country", "currency", "sector", "symbol")


def hhi(pesos: pd.Series) -> float:
    """Indice Herfindahl-Hirschman dos pesos (0 a 1)."""
    limpos = pd.to_numeric(pesos, errors="coerce").fillna(0.0)
    return float((limpos ** 2).sum())


def numero_efetivo(pesos: pd.Series) -> float:
    """Numero de posicoes iguais que teria a mesma concentracao. 0 se sem peso."""
    indice = hhi(pesos)
    return float(1.0 / indice) if indice > 0 else 0.0


def top_n(df: pd.DataFrame, n: int) -> float:
    """Participacao somada das n maiores posicoes."""
    if df.empty or n <= 0:
        return 0.0
    pesos = pd.to_numeric(df["weight_global"], errors="coerce").fillna(0.0)
    return float(pesos.nlargest(n).sum())


def gini(pesos: pd.Series) -> float:
    """Coeficiente de Gini dos pesos: 0 = perfeitamente igual."""
    limpos = pd.to_numeric(pesos, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    # Exclui pesos negativos: carteiras nao suportam posicoes curtas neste contexto.
    limpos = np.sort(limpos[limpos >= 0])
    total = limpos.sum()
    if total <= 0 or limpos.size == 0:
        return 0.0
    n = limpos.size
    indices = np.arange(1, n + 1)
    return float((2.0 * (indices * limpos).sum()) / (n * total) - (n + 1.0) / n)


def por_dimensao(df: pd.DataFrame, dimensao: str) -> pd.DataFrame:
    """Peso somado e contagem de ativos por valor da dimensao."""
    if df.empty:
        return pd.DataFrame(columns=[dimensao, "peso", "n_ativos"])

    agrupado = (df.groupby(dimensao, dropna=False)
                  .agg(peso=("weight_global", "sum"), n_ativos=("symbol", "count"))
                  .reset_index())
    return (agrupado.sort_values(["peso", dimensao], ascending=[False, True])
                    .reset_index(drop=True))


def resumo(df: pd.DataFrame) -> dict:
    """Concentracao consolidada por dimensao."""
    saida: dict[str, dict] = {}
    for dimensao in DIMENSOES:
        agrupado = por_dimensao(df, dimensao)
        pesos = agrupado["peso"] if not agrupado.empty else pd.Series(dtype=float)
        maior = agrupado.iloc[0] if not agrupado.empty else None
        saida[dimensao] = {
            "hhi": hhi(pesos),
            "numero_efetivo": numero_efetivo(pesos),
            "maior_peso": float(maior["peso"]) if maior is not None else 0.0,
            "maior_nome": (str(maior[dimensao]) if maior is not None else None),
        }
    return saida
