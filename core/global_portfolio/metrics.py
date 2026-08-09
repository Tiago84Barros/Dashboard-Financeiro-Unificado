"""Metricas agregadas do patrimonio, sempre acompanhadas da cobertura.

Duas decisoes metodologicas deliberadas:

1. Valuation agregado por EARNINGS YIELD ponderado, invertido no fim. A media
   aritmetica ponderada de P/L e matematicamente incorreta: uma empresa com
   lucro quase zero tem P/L enorme e domina a media.

2. Qualidade NAO e agregada num numero unico. Score B3, entry_score americano e
   score FII vem de metodologias e escalas diferentes. Normalizar por percentil
   dentro das proprias posicoes daria uma media proxima de 0,5 por construcao,
   sem significado. A qualidade e reportada por classe.

Coberto por tests/test_global_metrics.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.global_portfolio.fields import valor as campo_valor

COBERTURA_MINIMA = 0.60

# Nome do score de qualidade dentro de payload["metrics"], por classe.
_SCORE_POR_CLASSE: dict[str, str] = {
    "b3": "score",
    "us": "entry_score",
    "fii": "score",
}


@dataclass(frozen=True)
class MetricaAgregada:
    """Valor agregado mais a fracao do patrimonio que sustentou o calculo."""

    valor: float | None
    cobertura: float
    n_ativos: int

    @property
    def confiavel(self) -> bool:
        return self.valor is not None and self.cobertura >= COBERTURA_MINIMA


_VAZIA = MetricaAgregada(valor=None, cobertura=0.0, n_ativos=0)


def _pares(df: pd.DataFrame, campo: str) -> list[tuple[float, float]]:
    """(peso, valor) das linhas que possuem o campo canonico."""
    saida: list[tuple[float, float]] = []
    for linha in df.to_dict(orient="records"):
        v = campo_valor(linha.get("payload") or {}, linha.get("asset_class"), campo)
        if v is None:
            continue
        peso = float(linha.get("weight_global") or 0.0)
        if peso <= 0:
            continue
        saida.append((peso, v))
    return saida


def cobertura(df: pd.DataFrame, campo: str) -> float:
    """Fracao do peso total que possui o campo."""
    if df.empty:
        return 0.0
    total = float(pd.to_numeric(df["weight_global"], errors="coerce").fillna(0.0).sum())
    if total <= 0:
        return 0.0
    return sum(peso for peso, _ in _pares(df, campo)) / total


def valuation_agregado(df: pd.DataFrame, campo: str = "pe") -> MetricaAgregada:
    """Multiplo agregado via earnings yield ponderado, invertido no fim."""
    if df.empty:
        return _VAZIA

    total = float(pd.to_numeric(df["weight_global"], errors="coerce").fillna(0.0).sum())
    # Multiplo nao positivo (prejuizo) nao tem inverso interpretavel: sai do calculo.
    usaveis = [(peso, v) for peso, v in _pares(df, campo) if v > 0]
    if not usaveis or total <= 0:
        return _VAZIA

    peso_usado = sum(peso for peso, _ in usaveis)
    yield_ponderado = sum(peso * (1.0 / v) for peso, v in usaveis) / peso_usado
    if yield_ponderado <= 0:
        return MetricaAgregada(None, peso_usado / total, len(usaveis))

    return MetricaAgregada(
        valor=1.0 / yield_ponderado,
        cobertura=peso_usado / total,
        n_ativos=len(usaveis),
    )


def dy_consolidado(df: pd.DataFrame) -> MetricaAgregada:
    """DY do patrimonio: media ponderada aritmetica.

    Aqui a aritmetica esta correta — o DY e razao sobre preco e os pesos
    tambem sao sobre preco, entao a soma ponderada e o rendimento real.
    """
    if df.empty:
        return _VAZIA

    total = float(pd.to_numeric(df["weight_global"], errors="coerce").fillna(0.0).sum())
    usaveis = _pares(df, "dy")
    if not usaveis or total <= 0:
        return _VAZIA

    peso_usado = sum(peso for peso, _ in usaveis)
    return MetricaAgregada(
        valor=sum(peso * v for peso, v in usaveis) / peso_usado,
        cobertura=peso_usado / total,
        n_ativos=len(usaveis),
    )


def qualidade_por_classe(df: pd.DataFrame) -> dict[str, MetricaAgregada]:
    """Score medio ponderado DENTRO de cada classe. Nunca agregado entre classes."""
    if df.empty:
        return {}

    saida: dict[str, MetricaAgregada] = {}
    for classe in sorted(df["asset_class"].dropna().unique()):
        recorte = df[df["asset_class"] == classe]
        chave = _SCORE_POR_CLASSE.get(str(classe))
        total = float(pd.to_numeric(recorte["weight_global"],
                                    errors="coerce").fillna(0.0).sum())

        usaveis: list[tuple[float, float]] = []
        if chave:
            for linha in recorte.to_dict(orient="records"):
                bruto = (linha.get("payload") or {}).get("metrics", {}).get(chave)
                peso = float(linha.get("weight_global") or 0.0)
                if bruto is None or isinstance(bruto, bool) or peso <= 0:
                    continue
                try:
                    usaveis.append((peso, float(bruto)))
                except (TypeError, ValueError):
                    continue

        if not usaveis or total <= 0:
            saida[str(classe)] = _VAZIA
            continue

        peso_usado = sum(peso for peso, _ in usaveis)
        saida[str(classe)] = MetricaAgregada(
            valor=sum(peso * v for peso, v in usaveis) / peso_usado,
            cobertura=peso_usado / total,
            n_ativos=len(usaveis),
        )
    return saida
